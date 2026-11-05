"""Export a finished run as a self-contained OpenFOAM case (PRD RUN-3).

The zip holds everything plain OpenFOAM v2606 needs to reproduce the run on another
computer: ``system/``, ``constant/`` (with the STL), ``0.orig/`` and ``0/``, an ``Allrun``
that repeats WindLab's stages (and, for a sweep, every angle in turn), the force histories
WindLab recorded, a README with the inputs, results and the WindLab version / commit, and
the GPL / trade-mark notice for OpenFOAM. Processor folders and solution time directories
are never included (housekeeping has already removed them from a finished case; a note in
the README says so).

Safety: only regular files inside the case folder are read (no symlinks, no parents),
archive names are built from a fixed allow-list of relative paths, and the uncompressed
size is capped before anything is written.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import time
import zipfile
from pathlib import Path

from windlab import __version__, disclaimer
from windlab.case_builder import NU_SEA_LEVEL, RHO_SEA_LEVEL

DEFAULT_SIZE_CAP = 50 * 2**20  # uncompressed bytes
# top-level folders / files of a finished WindLab case that go into the zip
_INCLUDE_DIRS = ("system", "constant", "0.orig", "sweep", "postProcessing")
_INCLUDE_FILES = ("windlab_case.json", "windlab_report.json", "windlab_status.json")
_LOG_PREFIX = "log."
_SKIP_SUFFIXES = (".eMesh", ".tmp")
_SKIP_DIRS = {"polyMesh", "extendedFeatureEdgeMesh"}


class ExportError(ValueError):
    """Plain-language reason the case cannot be exported."""


def windlab_commit() -> str:
    """The git commit WindLab runs from, if the package sits in a checkout (else 'unknown')."""
    env = os.environ.get("WINDLAB_COMMIT")
    if env:
        return env.strip()[:40]
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return "unknown"
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                             text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha if re.fullmatch(r"[0-9a-f]{7,40}", sha) else "unknown"


def _safe_files(case: Path) -> list[tuple[Path, str]]:
    """(absolute path, archive-relative name) of every file that may go into the zip."""
    case = case.resolve()
    out: list[tuple[Path, str]] = []

    def add(p: Path) -> None:
        if p.is_symlink() or not p.is_file():
            return
        if p.name.endswith(_SKIP_SUFFIXES):
            return
        rel = p.resolve().relative_to(case)  # raises if it escaped
        out.append((p, rel.as_posix()))

    for name in _INCLUDE_FILES:
        add(case / name)
    for p in case.iterdir():
        if p.name.startswith(_LOG_PREFIX):
            add(p)
    for d in _INCLUDE_DIRS:
        base = case / d
        if not base.is_dir() or base.is_symlink():
            continue
        for root, dirs, files in os.walk(base, followlinks=False):
            r = Path(root)
            # never processor* folders nor the mesh (solution time directories sit at the case root and
            # are not walked at all; inside postProcessing a numeric folder is a function object's start time)
            dirs[:] = sorted(x for x in dirs if not x.startswith("processor") and x not in _SKIP_DIRS
                             and not (r / x).is_symlink())
            for f in sorted(files):
                add(r / f)
    return out


def _fmt(x, nd=4) -> str:
    return "-" if x is None else f"{x:.{nd}g}"


def readme(status: dict, case_meta: dict, report: dict | None, commit: str, note_files: str) -> str:
    p, r = status.get("params", {}), status.get("result") or {}
    sweep = p.get("kind") == "sweep"
    lines = [
        f"# WindLab case export - run {status.get('run_id', '?')}",
        "",
        "Uncalibrated prototype - not yet validated. For educational and preliminary design use; not for",
        "airworthiness or safety determinations.",
        "",
        f"Exported {time.strftime('%Y-%m-%d %H:%M')} by SenSym WindLab {__version__} (commit {commit}).",
        "The case runs with plain OpenFOAM v2606 (www.openfoam.com); see NOTICE-OPENFOAM.txt for the licence and",
        "trade-mark notice.",
        "",
        "## Inputs",
        "",
        f"- shape: {p.get('geometry_name') or '-'} (class: {p.get('geometry_class') or '-'})",
        f"- airspeed: {_fmt(p.get('airspeed'))} m/s",
        (f"- angles of attack: {', '.join(f'{a:g}' for a in p.get('angles') or [])} deg (sweep: one mesh, one solve "
         "per angle)" if sweep else f"- angle of attack: {_fmt(p.get('alpha_deg'))} deg"),
        f"- Reynolds number: {_fmt(p.get('reynolds'), 4)} (reference length {_fmt(p.get('reference_length'))} m)",
        f"- reference area: {_fmt(p.get('reference_area'), 5)} m^2 ({p.get('reference_how') or '-'})",
        f"- air: density {RHO_SEA_LEVEL} kg/m^3, kinematic viscosity {NU_SEA_LEVEL} m^2/s (sea level, 15 degC)",
        f"- mesh preset: {p.get('preset', 'quick')}; background cell {_fmt(case_meta.get('background_cell'))} m, "
        f"surface cell {_fmt(case_meta.get('surface_cell'))} m, {case_meta.get('n_layers')} boundary-layer cells",
        f"- processors used by WindLab: {p.get('n_procs')} (decomposeParDict); iteration cap {p.get('max_iterations')}",
        "- stop rule: WindLab stops simpleFoam once CL and CD have settled (ConvergenceMonitor, see",
        "  windlab_case.json 'convergence'); Allrun below runs to the iteration cap instead, so it takes longer",
        "  and may end a few iterations later than WindLab did.",
        "",
        "## Results WindLab recorded",
        "",
    ]
    if sweep:
        lines.append("| angle [deg] | CL | CD | CM | L/D | iterations | settled |")
        lines.append("|---:|---:|---:|---:|---:|---:|:--|")
        for pt in r.get("points") or []:
            if pt.get("status") == "done":
                settled = "yes" if pt.get("converged") else "no"
                lines.append(f"| {pt['alpha_deg']:g} | {_fmt(pt.get('cl'))} | {_fmt(pt.get('cd'))} | "
                             f"{_fmt(pt.get('cm'))} | {_fmt(pt.get('l_over_d'), 3)} | {pt.get('iterations')} | "
                             f"{settled} |")
            else:
                lines.append(f"| {pt.get('alpha_deg')} | - | - | - | - | - | {pt.get('status')} |")
    else:
        lines += [f"- CL = {_fmt(r.get('cl'))}, CD = {_fmt(r.get('cd'))}, CM = {_fmt(r.get('cm'))}, "
                  f"L/D = {_fmt(r.get('l_over_d'), 3)}",
                  f"- iterations: {r.get('iterations')} ({'settled' if r.get('converged') else 'did not settle'})"]
    cells = r.get("cells") or (report or {}).get("mesh", {}).get("cells") or "-"
    lines += [f"- mesh: {cells} cells; solver time {_fmt(r.get('solver_seconds'), 3)} s; "
              f"peak memory {_fmt(r.get('peak_memory_mb'), 3)} MB",
              "",
              "## What is in this folder",
              "",
              "- `system/`, `constant/` (with `triSurface/body.stl`, in metres), `0.orig/` and `0/`: the case exactly",
              "  as WindLab generated it from its template (every number clamped and checked by windlab.case_builder).",
              "- `Allrun`: reproduces the run with plain OpenFOAM v2606 (`./Allrun`; edit NPROCS at the top).",
              "- `postProcessing/`: the force-coefficient, residual and y+ histories WindLab recorded.",
              "- `log.*`: the logs of WindLab's own run. `windlab_*.json`: WindLab's inputs, report and status.",
              note_files,
              "- `NOTICE-OPENFOAM.txt`: licence and trade-mark notice.",
              "",
              "Validation of the method: see validation/ in the WindLab repository (2D NACA 0012 against NASA's",
              "Turbulence Modeling Resource; mesh study of the default wing).",
              "",
              "---",
              "",
              disclaimer.FOOTER,
              ""]
    return "\n".join(lines)


NOTICE = """OpenFOAM licence and trade-mark notice

This folder is an input case for OpenFOAM, generated by SenSym WindLab. It contains no
OpenFOAM source or object code. OpenFOAM is distributed under the GNU GPL v3 (the GNU
General Public License, version 3) by OpenCFD Ltd: you can obtain it, with its source
code, from https://www.openfoam.com and https://develop.openfoam.com. The case files
themselves are plain text dictionaries written by WindLab from its own templates and are
provided under the same open-source terms as WindLab.

OPENFOAM(R) is a registered trade mark of OpenCFD Limited, producer and distributor of the
OpenFOAM software via www.openfoam.com.

This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of
the OpenFOAM software via www.openfoam.com, and owner of the OPENFOAM(R) and OpenCFD(R) trade
marks.
"""

# The two sentences OpenCFD's trade-mark guidelines ask for, as the page, the reports and the docs show them.
TRADEMARK_DISCLAIMER = ("This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of the "
                        "OpenFOAM software via www.openfoam.com, and owner of the OPENFOAM® and OpenCFD® trade marks.")
TRADEMARK_ACKNOWLEDGEMENT = ("OPENFOAM® is a registered trade mark of OpenCFD Limited, producer and distributor of the "
                             "OpenFOAM software via www.openfoam.com.")
GPL_NOTICE = "OpenFOAM is distributed under the GNU GPL v3."


def allrun(case_meta: dict, n_procs: int) -> str:
    """A bash Allrun in the style of the OpenFOAM tutorials. No user text ever enters it:
    the only values are the processor count and the sweep angles (numbers)."""
    n = int(max(1, min(int(n_procs), 64)))
    sweep = (case_meta.get("sweep") or {}).get("points") or []
    head = f"""#!/bin/sh
# Allrun - reproduces this WindLab run with plain OpenFOAM v2606.
#   source /usr/lib/openfoam/openfoam2606/etc/bashrc   (or your own OpenFOAM installation)
#   ./Allrun
# NPROCS cores are used (decomposeParDict is rewritten accordingly). The solver runs to the
# iteration cap in system/controlDict; WindLab itself stopped earlier, once CL and CD settled.
cd "${{0%/*}}" || exit 1
. "${{WM_PROJECT_DIR:?Source the OpenFOAM bashrc first}}/bin/tools/RunFunctions"
NPROCS=${{NPROCS:-{n}}}

foamDictionary -entry numberOfSubdomains -set "$NPROCS" system/decomposeParDict > /dev/null

runApplication blockMesh
runApplication surfaceFeatureExtract
runApplication decomposePar -force
runParallel snappyHexMesh -overwrite
restore0Dir -processor
runParallel checkMesh -constant
"""
    if not sweep:
        return head + """runParallel potentialFoam -writephi
runParallel simpleFoam
runApplication reconstructParMesh -constant
runApplication reconstructPar -latestTime
echo "Done. Force coefficients: postProcessing/forceCoeffs1/*/coefficient.dat"
"""
    angles = " ".join(f"{i}:{pt['alpha_deg']:g}" for i, pt in enumerate(sweep))
    return head + f"""
# One angle after another on the same mesh: swap in that angle's controlDict and inlet
# velocity (sweep/p<i>/), start from fresh fields, solve, and keep its postProcessing.
runApplication reconstructParMesh -constant
for pair in {angles}; do
    i=${{pair%%:*}}; alpha=${{pair#*:}}
    echo "== angle $i: $alpha deg"
    cp sweep/p$i/controlDict system/controlDict
    cp sweep/p$i/U 0.orig/U
    rm -rf postProcessing
    restore0Dir -processor
    runParallel -s "p$i" potentialFoam -writephi
    runParallel -s "p$i" simpleFoam
    rm -rf "sweep/p$i/postProcessing"
    mv postProcessing "sweep/p$i/postProcessing"
done
echo "Done. Force coefficients per angle: sweep/p<i>/postProcessing/forceCoeffs1/*/coefficient.dat"
"""


def export_case(case_dir: Path, status: dict, size_cap: int = DEFAULT_SIZE_CAP, commit: str | None = None) -> bytes:
    """The zip (as bytes) of a finished run's case folder. Raises ExportError when too large or incomplete."""
    case = Path(case_dir)
    if not case.is_dir() or case.is_symlink():
        raise ExportError("This run's files were not found. It may have been deleted.")
    meta_f = case / "windlab_case.json"
    if not meta_f.is_file() or not (case / "system").is_dir():
        raise ExportError("This run's case files are incomplete and cannot be exported.")
    case_meta = json.loads(meta_f.read_text())
    report = None
    if (case / "windlab_report.json").is_file():
        report = json.loads((case / "windlab_report.json").read_text())
    files = _safe_files(case)
    total = sum(p.stat().st_size for p, _ in files)
    if total > size_cap:
        raise ExportError(f"This case is {total / 2**20:.0f} MB, more than the {size_cap / 2**20:.0f} MB WindLab will "
                          "zip. The STL file is probably very large; export it from CAD with fewer triangles.")
    run_id = str(status.get("run_id", case.name))
    root = f"windlab-case-{run_id[:8]}"
    note = ("- No solution time directories or processor folders are included: WindLab deletes them when a run "
            "finishes to save disk space (a Quick run writes a few hundred MB). Run `./Allrun` to recompute the "
            "fields.")
    buf = io.BytesIO()
    now = time.localtime()[:6]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p, rel in files:
            z.write(p, f"{root}/{rel}")
        # 0/ for people who expect it: a copy of the initial fields
        for p, rel in files:
            if rel.startswith("0.orig/"):
                z.write(p, f"{root}/0/{rel[len('0.orig/'):]}")
        info = zipfile.ZipInfo(f"{root}/Allrun", date_time=now)
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        z.writestr(info, allrun(case_meta, int(status.get("params", {}).get("n_procs") or 4)))
        text = readme(status, case_meta, report, commit or windlab_commit(), note)
        z.writestr(f"{root}/README.md", text)
        z.writestr(f"{root}/NOTICE-OPENFOAM.txt", NOTICE)
    return buf.getvalue()


def zip_name(status: dict) -> str:
    created = status.get("created") or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(created))
    return f"windlab-case-{day}-{str(status.get('run_id', 'run'))[:8]}.zip"


__all__ = ["DEFAULT_SIZE_CAP", "GPL_NOTICE", "NOTICE", "TRADEMARK_ACKNOWLEDGEMENT", "TRADEMARK_DISCLAIMER",
           "ExportError", "allrun", "export_case", "readme", "windlab_commit", "zip_name"]
