"""STEP import (PRD GEO-2): read a SolidWorks STEP export, tessellate it, then run the STL checks.

OpenCASCADE (the ``cadquery-ocp`` package, optional extra ``pip install ".[step]"``) does the
reading in a separate, resource-limited process (:mod:`windlab.geometry._step_worker`,
started with ``python -I`` under ``prlimit``, like the STL worker). The worker writes a
binary STL in the file's own units; :func:`windlab.geometry.upload.check_stl` then applies
the units the student confirmed, scales to metres and runs the usual watertight / manifold
/ crossing checks, so everything after the upload is unchanged.

Units: the worker reads the length unit the file declares and the report pre-selects it;
the student still confirms (the size sanity check stays). Several solids: touching ones
are fused into one; separate ones are explained and the largest is used unless the student
asks for all of them (``bodies="all"``).

When OCP is not importable every entry point degrades to a clear message
(:data:`UNAVAILABLE`); nothing else in WindLab depends on it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from windlab.geometry.upload import (
    MAX_FILE_BYTES,
    UNIT_NAMES,
    UNIT_TO_METRES,
    GeometryReport,
    UploadError,
    _fmt_length,
    _prlimit,
    check_stl,
)

_WORKER = Path(__file__).with_name("_step_worker.py")
WORKER_ADDRESS_SPACE = 3 * 1024**3  # OpenCASCADE maps more libraries than numpy alone
WORKER_CPU_SECONDS = 120
WORKER_WALL_SECONDS = 150
MAX_STEP_TRIANGLES = 1_500_000  # the tessellation is coarsened until it fits (then the STL cap applies)
STEP_SUFFIXES = (".step", ".stp")

UNAVAILABLE = ("STEP import is not installed on this computer. Either export the part from SolidWorks as STL "
               "(File > Save As > STL, one solid body) and upload that, or add STEP support: in the Ubuntu window "
               "run  sudo apt install libgl1  and  pip install \"~/windlab[step]\"  (with the WindLab environment "
               "active), then restart WindLab.")
INSTALL_HINT = "sudo apt install libgl1  and  pip install \"~/windlab[step]\""


def available() -> bool:
    """Is the OpenCASCADE package installed? (cheap: no import; the worker reports if it cannot load)"""
    try:
        return importlib.util.find_spec("OCP") is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def probe(timeout: float = 60.0) -> tuple[bool, str]:
    """Really load OCP in a child process (``windlab doctor``): (ok, plain-language detail)."""
    if not available():
        return False, "the OpenCASCADE package (cadquery-ocp) is not installed"
    try:
        r = subprocess.run([sys.executable, "-I", "-c", "import OCP; print(getattr(OCP, '__version__', '?'))"],
                           capture_output=True, text=True, timeout=timeout,
                           env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"OpenCASCADE could not be started ({e})"
    if r.returncode == 0 and r.stdout.strip():
        return True, f"OpenCASCADE {r.stdout.strip()} (cadquery-ocp)"
    err = (r.stderr or "").strip().splitlines()
    last = err[-1] if err else "unknown error"
    if "libGL" in last or "libgl" in last.lower():
        return False, f"OpenCASCADE is installed but cannot load ({last[:120]}): the system package libgl1 is missing"
    return False, f"OpenCASCADE is installed but cannot load ({last[:160]})"


def is_step_file(path: str | os.PathLike, name: str | None = None) -> bool:
    """A STEP file by its first bytes (``ISO-10303-21``), else by the uploaded name's suffix."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(256)
    except OSError:
        head = b""
    if head.lstrip()[:12].startswith(b"ISO-10303-21"):
        return True
    if head.lstrip()[:5].lower() == b"solid" or (len(head) >= 84 and not head.lstrip()[:1].isalpha()):
        return False  # text or binary STL
    return bool(name) and str(name).lower().endswith(STEP_SUFFIXES)


def _run_worker(path: Path, out_stl: Path, max_triangles: int, bodies: str, wall_seconds: float) -> dict:
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "HOME": "/nonexistent"}
    fsize = 84 + 50 * int(max_triangles)
    argv = [
        _prlimit(),
        f"--as={WORKER_ADDRESS_SPACE}",
        f"--cpu={WORKER_CPU_SECONDS}:{WORKER_CPU_SECONDS + 5}",
        f"--fsize={fsize}",
        "--core=0",
        "--",
        sys.executable, "-I", str(_WORKER), str(path), str(out_stl), str(int(max_triangles)), bodies,
    ]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, env=env, cwd="/",
                              timeout=wall_seconds, close_fds=True)
    except subprocess.TimeoutExpired:
        raise UploadError("Reading this STEP file took too long. Try a simpler export (fewer features, no threads "
                          "or text), or export the part as STL instead.") from None
    lines = [ln for ln in proc.stdout.decode(errors="replace").splitlines() if ln.strip()]
    data = None
    if lines:
        try:
            data = json.loads(lines[-1])
        except json.JSONDecodeError:
            data = None
    if proc.returncode == 0 and isinstance(data, dict) and "error" not in data:
        return data
    if isinstance(data, dict) and data.get("missing"):
        raise UploadError(UNAVAILABLE)
    if isinstance(data, dict) and "error" in data:
        raise UploadError(f"This STEP file can't be used: {data['error']}. Export the part again from SolidWorks "
                          "(File > Save As > STEP AP214, one solid body), or export it as STL.")
    if proc.returncode in (-9, -24) or b"MemoryError" in proc.stderr:
        raise UploadError("This STEP file is too big or too complex to read here. Export the part with fewer "
                          "features, or export it as STL with a coarse resolution.")
    tail = proc.stderr.decode(errors="replace").strip().splitlines()[-1:] or [""]
    if "libGL" in tail[0] or "No module named 'OCP'" in tail[0]:
        raise UploadError(UNAVAILABLE)
    raise UploadError("This file could not be read as a STEP file. Export the part again from SolidWorks "
                      "(File > Save As > STEP AP214, one solid body), or export it as STL.")


def check_step(
    path: str | os.PathLike,
    units: str | None,
    *,
    output: str | os.PathLike,
    bodies: str = "largest",
    max_bytes: int = MAX_FILE_BYTES,
    max_triangles: int = MAX_STEP_TRIANGLES,
    wall_seconds: float = WORKER_WALL_SECONDS,
) -> GeometryReport:
    """Read a STEP file and return the usual :class:`GeometryReport` (with ``source`` filled in).

    ``units`` is the student's choice, or ``None`` to take the unit the file declares. The
    cleaned, metre-scaled STL goes to ``output`` exactly as :func:`check_stl` writes it.
    """
    if not available():
        raise UploadError(UNAVAILABLE)
    if bodies not in ("largest", "all"):
        raise UploadError("Choose whether to use the largest body or all bodies.")
    if units is not None and units not in UNIT_TO_METRES:
        raise UploadError("Please choose the units the part was drawn in: mm, cm, m or in.")
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise UploadError("The uploaded file was not found.")
    size = p.stat().st_size
    if size > max_bytes:
        raise UploadError(f"This file is {size / 2**20:.0f} MB; the limit is {max_bytes / 2**20:.0f} MB. "
                          "Export only the part you need, without the rest of the assembly.")
    if size == 0:
        raise UploadError("The uploaded file is empty.")
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise UploadError("internal error: the output file already exists")
    tess = output.with_name(output.name + ".step-tess.stl")
    tess.unlink(missing_ok=True)
    try:
        info = _run_worker(p, tess, max_triangles, bodies, wall_seconds)
        declared = info.get("unit_declared")
        chosen = units or declared or "mm"
        report = check_stl(tess, chosen, max_triangles=max_triangles, wall_seconds=wall_seconds, output=output)
    finally:
        tess.unlink(missing_ok=True)
    return annotate(report, info, units_from_file=units is None)


def annotate(report: GeometryReport, info: dict, units_from_file: bool) -> GeometryReport:
    """Add the STEP facts (unit, solids, healing, fusing) to the STL report, in plain words."""
    report.format = "step"
    declared = info.get("unit_declared")
    n_solids, after, used = int(info.get("solids", 0)), int(info.get("bodies_after_fuse", 0)), info.get("bodies_used")
    report.source = {
        "format": "step",
        "unit_declared": declared,
        "unit_name_in_file": info.get("unit_name_in_file"),
        "converted_to_metres": bool(info.get("converted_to_metres")),
        "units_from_file": bool(units_from_file),
        "solids": n_solids,
        "faces": int(info.get("faces", 0)),
        "healed": bool(info.get("healed")),
        "valid_before": info.get("valid_before"),
        "valid_after": info.get("valid_after"),
        "fused": bool(info.get("fused")),
        "bodies_available": after,
        "bodies_used": used,
        "bodies": info.get("bodies") or [],
        "triangles": int(info.get("triangles", report.triangles)),
        "deflection": info.get("deflection"),
    }
    lines = []
    if info.get("converted_to_metres"):
        lines.append(f"The STEP file is in {info.get('unit_name_in_file') or 'an unusual unit'}; WindLab converted "
                     "it to metres, so the units choice is metres.")
    elif declared is None:
        lines.append("The STEP file does not say which units it uses; millimetres (the STEP default) were assumed. "
                     "Check the size below and change the units if it is wrong.")
    elif units_from_file:
        name = UNIT_NAMES[declared]
        lines.append(f"The STEP file says it is in {name}, so WindLab read it in {name}. Check the size below: if it "
                     "is wrong, choose other units above and check the file again.")
    elif report.units != declared:
        lines.append(f"The STEP file says {UNIT_NAMES[declared]}, but you chose {UNIT_NAMES[report.units]}; "
                     f"WindLab used {UNIT_NAMES[report.units]}.")
    one = n_solids == 1
    lines.append(f"Read {n_solids} solid bod{'y' if one else 'ies'} with {info.get('faces', 0)} faces from the STEP "
                 f"file and tessellated {'it' if one else 'them'} into {report.triangles:,d} triangles.")
    if info.get("healed"):
        lines.append("Small defects in the CAD surfaces were repaired automatically (OpenCASCADE ShapeFix).")
    if n_solids == 0:
        report.problems.insert(0, "The STEP file contains no solid body, only surfaces. In SolidWorks, make sure the "
                                  "part is a solid (Insert > Boss/Base > Thicken or Knit with 'Create solid'), then "
                                  "export it again.")
        report.ok = False
        report.messages = [m for m in report.messages if "can be meshed" not in m]
        if not any("can't be simulated" in m for m in report.messages):
            report.messages.append("This part can't be simulated until the problems below are fixed.")
    elif info.get("fused"):
        lines.append(f"The {n_solids} bodies touch each other, so WindLab joined them into one solid"
                     + (f" ({after} remain separate)." if after > 1 else "."))
    if after > 1:
        sizes = report.source["bodies"]
        f = UNIT_TO_METRES[report.units]
        if used == "largest":
            report.warnings.insert(0, (
                f"The file holds {after} separate bodies that do not touch (an assembly, or several parts in one "
                f"file). WindLab is using the largest one ({_fmt_length(sizes[0]['longest'] * f)} long). To test "
                "another body, export it on its own from SolidWorks; to put all of them in the tunnel together, "
                "press \"Use all bodies\"."))
        else:
            report.warnings.insert(0, (
                f"The file holds {after} separate bodies that do not touch; all of them are in the wind tunnel "
                "together. Their forces are added up and the reference values come from the box around all of "
                "them."))
            report.warnings = [w for w in report.warnings if "separate pieces" not in w]
    report.messages = lines + report.messages
    return report


__all__ = ["INSTALL_HINT", "UNAVAILABLE", "annotate", "available", "check_step", "is_step_file", "probe"]
