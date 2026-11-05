"""Check an uploaded STL file before it is allowed anywhere near a simulation.

The file is parsed in a separate, resource-limited Python process
(:mod:`windlab.geometry._stl_worker`, started with ``python -I``) so that a hostile or
broken upload cannot exhaust memory or CPU in the server (PRD SEC-*, GEO-1):

* address space (RLIMIT_AS) 2 GB, CPU time 60 s, no file writes (except the cleaned copy,
  size-capped), no core dumps, wall-clock timeout - set with util-linux ``prlimit``,
* at most 200 MB per file and 10 million triangles.

The caller must say which units the file is in (mm, cm, m or in). The report gives the
size in metres and flags sizes that look like a units mistake, e.g.
"This part would be 300 m long - did you mean millimetres?".

Checks: watertight (no open edges), manifold (no edge shared by more than two
triangles), consistent normals (neighbouring triangles agree on inside/outside),
outward orientation, degenerate triangles, number of separate pieces, bounding box,
and self-intersections (surfaces crossing each other: two overlapping bodies exported
without being combined). The crossing check is time-limited in the worker and skipped
above a triangle count where it would be too slow; both cases are reported as a warning.
A closed part is also measured for local thickness (plates, fins, walls), which the mesh
settings and the page use (:mod:`windlab.geometry.thin`).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TRIANGLES = 10_000_000
WORKER_ADDRESS_SPACE = 2 * 1024**3
WORKER_CPU_SECONDS = 60
WORKER_WALL_SECONDS = 90

Units = Literal["mm", "cm", "m", "in"]
UNIT_TO_METRES: dict[str, float] = {"mm": 1e-3, "cm": 1e-2, "m": 1.0, "in": 0.0254}
UNIT_NAMES = {"mm": "millimetres", "cm": "centimetres", "m": "metres", "in": "inches"}

# Longest dimension outside this range (metres) is probably a units mistake.
MIN_PLAUSIBLE_M = 0.01
MAX_PLAUSIBLE_M = 20.0

_WORKER = Path(__file__).with_name("_stl_worker.py")


class UploadError(ValueError):
    """The file could not be checked at all; ``str(e)`` is a plain-language message."""


@dataclass
class GeometryReport:
    """Result of checking one STL file. ``messages`` are plain language for students."""

    ok: bool  # geometry can be meshed (watertight, manifold, consistent)
    units: str
    format: str
    triangles: int
    size_m: tuple[float, float, float]  # bounding-box extent in x, y, z
    bounds_min_m: tuple[float, float, float]
    bounds_max_m: tuple[float, float, float]
    longest_m: float
    watertight: bool
    manifold: bool
    consistent_normals: bool
    outward_normals: bool
    pieces: int
    size_suspicious: bool  # needs an explicit "yes, the units are right" before a run
    suggested_units: str | None
    surface_area_m2: float
    volume_m3: float
    counts: dict = field(default_factory=dict)  # raw counts from the parser
    problems: list[str] = field(default_factory=list)  # must be fixed before a run
    warnings: list[str] = field(default_factory=list)  # worth knowing, not blocking
    messages: list[str] = field(default_factory=list)  # summary lines, in display order
    # surfaces crossing each other: True / False, or None when the check was skipped or timed out
    self_intersecting: bool | None = None
    self_intersection_check: str = "skipped"  # "done" | "skipped" | "timed out"
    # where the triangles came from when not from an STL: a STEP file's facts (windlab.geometry.step)
    source: dict | None = None
    # mirror symmetry about the box's mid-planes {"x", "y", "z"}: True when at least SYMMETRY_MATCH of the
    # vertices have a mirror twin (a z-symmetric part must have zero lift at 0 degrees)
    mirror_symmetric: dict | None = None
    # local thickness by ray casting (metres; closed surfaces only): "status", "rays", "hits", "min",
    # "min_plate" and "plate" = [[thickness, area_m2], ...] of the plate-like samples, thinnest first
    # (windlab.geometry.thin turns it into the mesh decision)
    thickness: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


SYMMETRY_MATCH = 0.95  # share of mirrored vertices that must coincide with a vertex


# --------------------------------------------------------------------------- worker


def _prlimit() -> str:
    exe = shutil.which("prlimit", path="/usr/bin:/bin")
    if exe is None:  # util-linux is part of every Ubuntu install; refuse rather than run unlimited
        raise UploadError("WindLab can't check files on this computer: the 'prlimit' tool (util-linux) is missing.")
    return exe


def _run_worker(
    path: Path, max_triangles: int, wall_seconds: float, out: tuple[Path, float] | None = None
) -> dict:
    """Run the parser under resource limits set by util-linux ``prlimit``.

    ``prlimit`` sets the limits on itself and then execs the worker, so they hold before
    any parsing, and no Python code runs between fork and exec in this (threaded) server
    process - which ``preexec_fn`` would need, and which can deadlock.
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        # One thread each: BLAS thread pools reserve memory and would hit RLIMIT_AS.
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    args = [str(path), str(int(max_triangles))]
    fsize = 0  # no file writes at all, unless a cleaned copy was asked for
    if out is not None:
        out_path, scale = out
        args += [str(out_path), repr(float(scale))]
        # Output is binary STL, at most the input's triangle count (ASCII facets are > 40 bytes).
        fsize = 84 + 50 * min(int(max_triangles), path.stat().st_size // 40 + 1)
    argv = [
        _prlimit(),
        f"--as={WORKER_ADDRESS_SPACE}",
        f"--cpu={WORKER_CPU_SECONDS}:{WORKER_CPU_SECONDS + 5}",
        f"--fsize={fsize}",
        "--core=0",
        "--",
        sys.executable, "-I", str(_WORKER), *args,
    ]
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=env,
            cwd="/",
            timeout=wall_seconds,
            close_fds=True,
        )
    except subprocess.TimeoutExpired:
        raise UploadError("Checking this file took too long. Try exporting it with fewer triangles.") from None

    out = proc.stdout.decode(errors="replace").strip().splitlines()
    data = None
    if out:
        try:
            data = json.loads(out[-1])
        except json.JSONDecodeError:
            data = None
    if proc.returncode == 0 and isinstance(data, dict) and "error" not in data:
        return data
    if isinstance(data, dict) and "error" in data:
        msg = data["error"]
        raise UploadError(f"This file can't be used: {msg}. {REEXPORT}")
    if proc.returncode in (-9, -24) or b"MemoryError" in proc.stderr:  # SIGKILL / SIGXCPU / out of memory
        raise UploadError("This file is too big or too complex to check. Try exporting it with fewer triangles.")
    raise UploadError(f"This file could not be read as an STL file. {REEXPORT}")


REEXPORT = "Export the part again from SolidWorks (File > Save As > STL, one solid body) and upload that file."


# --------------------------------------------------------------------------- report


def _fmt_length(m: float) -> str:
    if m >= 1.0:
        return f"{m:.3g} m"
    if m >= 0.01:
        return f"{m * 100:.3g} cm"
    return f"{m * 1000:.3g} mm"


def _suggest_units(longest_file_units: float, stated: str) -> str | None:
    """The unit that would make the part a plausible wind-tunnel model (closest to ~0.3 m)."""
    best = None
    for u, f in UNIT_TO_METRES.items():
        if u == stated:
            continue
        size = longest_file_units * f
        if MIN_PLAUSIBLE_M <= size <= MAX_PLAUSIBLE_M:
            score = abs(math.log10(size / 0.3))
            if best is None or score < best[0]:
                best = (score, u)
    return best[1] if best else None


def build_report(raw: dict, units: str) -> GeometryReport:
    """Turn the worker's raw counts into a plain-language report (pure function)."""
    f = UNIT_TO_METRES[units]
    lo = tuple(float(x) * f for x in raw["bounds_min"])
    hi = tuple(float(x) * f for x in raw["bounds_max"])
    size = tuple(b - a for a, b in zip(lo, hi, strict=True))
    longest = max(size)
    longest_file = longest / f
    volume = raw["signed_volume"] * f**3
    problems, warnings, messages = [], [], []

    watertight = raw["open_edges"] == 0
    manifold = raw["nonmanifold_edges"] == 0
    consistent = raw["inconsistent_edges"] == 0
    outward = volume > 0
    crossing_check = str(raw.get("self_intersection_check", "skipped"))
    crossings = int(raw.get("self_intersections", 0) or 0)
    self_intersecting = (crossings > 0) if crossing_check == "done" or crossings else None

    messages.append(
        f"Read {raw['triangles']:,d} triangles ({raw['format']} STL). With the file in {UNIT_NAMES[units]}, "
        f"the part is {_fmt_length(size[0])} x {_fmt_length(size[1])} x {_fmt_length(size[2])} (x, y, z)."
    )

    suspicious = not (MIN_PLAUSIBLE_M <= longest <= MAX_PLAUSIBLE_M)
    suggested = _suggest_units(longest_file, units) if suspicious else None
    if suspicious:
        msg = f"This part would be {_fmt_length(longest)} long"
        msg += f" - did you mean {UNIT_NAMES[suggested]}?" if suggested else " - please check the units."
        warnings.append(msg)

    if not watertight:
        problems.append(
            f"The surface has holes: {raw['open_edges']:,d} triangle edges are not joined to a neighbour. "
            "The part must be a closed solid - in SolidWorks, export a solid body, not a surface."
        )
    if not manifold:
        problems.append(
            f"{raw['nonmanifold_edges']:,d} edges are shared by more than two triangles (parts touching along an "
            "edge, or duplicate faces). Combine the bodies into one solid before exporting."
        )
    if not consistent:
        problems.append(
            f"{raw['inconsistent_edges']:,d} edges have triangles that disagree about which side is outside "
            "(flipped triangles). Re-export the part from CAD."
        )
    if self_intersecting:
        problems.append(
            f"The part's surfaces cross each other ({crossings:,d} pairs of triangles cut through one another). "
            "This usually means two overlapping bodies were exported as one file. In SolidWorks, combine them into "
            "one solid body (Insert > Features > Combine), then re-export it as one solid body."
        )
    elif crossing_check == "skipped":
        warnings.append(
            f"This file has too many triangles ({raw['triangles']:,d}) for the surface-crossing check, so WindLab "
            "could not tell whether the part's surfaces cross each other. If meshing fails later, export the part "
            "with fewer triangles (a coarser STL) and upload it again."
        )
    elif crossing_check == "timed out":
        warnings.append(
            "The surface-crossing check ran out of time, so WindLab could not tell whether the part's surfaces cross "
            "each other. If meshing fails later, export the part with fewer triangles and upload it again."
        )
    if watertight and manifold and consistent and not outward:
        warnings.append("The surface is inside-out (all triangles face inwards). WindLab will flip it.")
    if raw["degenerate_triangles"]:
        warnings.append(f"{raw['degenerate_triangles']:,d} triangles have zero area; they will be ignored.")
    if raw["pieces"] > 1:
        warnings.append(
            f"The file contains {raw['pieces']} separate pieces. They will all be in the wind tunnel together."
        )
    if raw.get("flipped_stored_normals"):
        warnings.append(
            f"{raw['flipped_stored_normals']:,d} stored normal vectors disagree with the triangle order; "
            "the triangle order is used."
        )

    ok = watertight and manifold and consistent and not self_intersecting
    if ok:
        messages.append("The surface is closed and consistent - it can be meshed.")
    else:
        messages.append("This part can't be simulated until the problems below are fixed.")
    match = raw.get("mirror_match")
    symmetric = None
    if isinstance(match, (list, tuple)) and len(match) == 3:
        symmetric = {axis: float(m) >= SYMMETRY_MATCH for axis, m in zip("xyz", match, strict=True)}
    thickness = _scale_thickness(raw.get("thickness"), f)

    return GeometryReport(
        ok=ok,
        units=units,
        format=raw["format"],
        triangles=int(raw["triangles"]),
        size_m=size,
        bounds_min_m=lo,
        bounds_max_m=hi,
        longest_m=longest,
        watertight=watertight,
        manifold=manifold,
        consistent_normals=consistent,
        outward_normals=outward,
        pieces=int(raw["pieces"]),
        size_suspicious=suspicious,
        suggested_units=suggested,
        surface_area_m2=raw["surface_area"] * f**2,
        volume_m3=abs(volume),
        counts={k: raw[k] for k in ("vertices", "edges", "open_edges", "nonmanifold_edges", "inconsistent_edges",
                                    "degenerate_triangles", "flipped_stored_normals")}
        | {"self_intersections": crossings, "mirror_match": list(match) if match else None},
        problems=problems,
        warnings=warnings,
        messages=messages,
        self_intersecting=self_intersecting,
        self_intersection_check=crossing_check,
        mirror_symmetric=symmetric,
        thickness=thickness,
    )


def _scale_thickness(raw: dict | None, f: float) -> dict | None:
    """The worker's thickness block in metres (lengths x f, areas x f^2), or None when it was not measured."""
    if not isinstance(raw, dict) or raw.get("status") not in ("done", "timed out"):
        return None

    def length(x):
        return None if x is None else float(x) * f

    pairs = []
    for item in raw.get("plate") or []:
        try:
            t, a = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(t) and math.isfinite(a) and t > 0 and a > 0:
            pairs.append([t * f, a * f * f])
    pairs.sort()
    samples = {"index": [], "t": [], "plate": []}
    s = raw.get("samples")
    if isinstance(s, dict):
        try:
            index = [int(i) for i in s.get("index", [])]
            t = [float(x) * f for x in s.get("t", [])]
            plate = [int(bool(p)) for p in s.get("plate", [])]
        except (TypeError, ValueError):
            index, t, plate = [], [], []
        if len(index) == len(t) == len(plate) and all(i >= 0 for i in index) and all(math.isfinite(x) for x in t):
            samples = {"index": index, "t": t, "plate": plate}
    return {"status": str(raw["status"]), "rays": int(raw.get("rays", 0)), "hits": int(raw.get("hits", 0)),
            "min": length(raw.get("min")), "min_plate": length(raw.get("min_plate")),
            "max_ray": length(raw.get("max_ray")), "plate": pairs, "samples": samples}


def check_stl(
    path: str | os.PathLike,
    units: Units,
    *,
    max_bytes: int = MAX_FILE_BYTES,
    max_triangles: int = MAX_TRIANGLES,
    wall_seconds: float = WORKER_WALL_SECONDS,
    output: str | os.PathLike | None = None,
) -> GeometryReport:
    """Check an uploaded STL file. Raises :class:`UploadError` with a plain-language message.

    With ``output``, a part that passes the checks is also written there as a binary STL
    in metres (scaled from ``units``), with zero-area triangles removed and an inside-out
    surface turned the right way out. This is the file the case builder must use. The
    output must not exist yet; it is removed again if the part fails the checks.
    """
    if units not in UNIT_TO_METRES:
        raise UploadError("Please choose the units the part was drawn in: mm, cm, m or in.")
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise UploadError("The uploaded file was not found.")
    size = p.stat().st_size
    if size > max_bytes:
        raise UploadError(
            f"This file is {size / 2**20:.0f} MB; the limit is {max_bytes / 2**20:.0f} MB. "
            "Export the STL with a coarser resolution."
        )
    if size == 0:
        raise UploadError("The uploaded file is empty.")
    if output is not None:
        output = Path(output)
        if output.exists() or output.is_symlink():
            raise UploadError("internal error: the output file already exists")
    raw = _run_worker(p, max_triangles, wall_seconds, (output, UNIT_TO_METRES[units]) if output else None)
    report = build_report(raw, units)
    if output is not None and not report.ok:
        output.unlink(missing_ok=True)
    return report


__all__ = [
    "GeometryReport",
    "MAX_FILE_BYTES",
    "MAX_TRIANGLES",
    "UNIT_NAMES",
    "UNIT_TO_METRES",
    "UploadError",
    "build_report",
    "check_stl",
]
