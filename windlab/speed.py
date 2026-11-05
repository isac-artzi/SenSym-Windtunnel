"""How fast this computer runs WindLab, and the time estimate shown before a run (task 010, step 4).

Every finished run (and ``windlab benchmark``) leaves a record in ``<data>/machine_speed.json``:
the solver's seconds per cell-iteration and the meshing's seconds per cell, normalised to
``REFERENCE_PROCS`` ranks with the same imperfect-scaling law as the live time-left estimate
(:mod:`windlab.app.eta`). An estimate for a preset is then

    safety x (cells x mesh_speed + angles x iterations x cells x solve_speed)

with the cell count scaled from the shape's own Quick mesh when one has been run (the three
presets are one refinement family: the mesh study measured Standard at 2.17x and the finer
level at 4.65x the Quick cells), and the expected iterations per preset from the everyday
stop rule with a cold start. Before any run on this computer the speeds default to a slow
laptop under WSL2, so the first estimate errs long.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from pathlib import Path

from windlab.case_builder import PRESETS, Preset

RECORDS_FILE = "machine_speed.json"
MAX_RECORDS = 40
REFERENCE_PROCS = 4
SCALING_EXPONENT = 0.8  # time ~ (REFERENCE_PROCS / ranks) ** 0.8, as in windlab.app.eta
SAFETY = 1.25

# The 14-core Xeon test workstation under WSL2 (2026-11-03, 4 ranks): about 160 s of solver for 372k cells
# x 392 iterations = 1.1e-6 s per cell-iteration, 34 s of meshing / 372k cells = 9e-5 s per
# cell. The defaults are set a third slower than that.
DEFAULT_SOLVE_S_PER_CELL_ITERATION = 1.5e-6
DEFAULT_MESH_S_PER_CELL = 1.2e-4

# Cold-start iterations to expect with the everyday stop rule (Quick settled at 245 / 194
# iterations in the profile runs; coarser-than-asymptotic meshes need a little more).
EXPECTED_ITERATIONS = {Preset.QUICK: 300, Preset.STANDARD: 350, Preset.DETAILED: 400}
CELLS_VS_QUICK = {Preset.QUICK: 1.0, Preset.STANDARD: 2.17, Preset.DETAILED: 4.65}
PEAK_MEMORY_GB = {Preset.QUICK: 1.1, Preset.STANDARD: 2.0, Preset.DETAILED: 6.5}  # mesh study, 4 ranks

DETAILED_MIN_RAM_GB = 24.0
DETAILED_MAX_QUICK_SECONDS = 90.0


def core_scale(n_procs: int) -> float:
    return (REFERENCE_PROCS / max(1, int(n_procs))) ** SCALING_EXPONENT


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / RECORDS_FILE


def records(data_dir: Path) -> list[dict]:
    p = _path(data_dir)
    if not p.is_file() or p.is_symlink():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data.get("records", []) if isinstance(r, dict)] if isinstance(data, dict) else []


def record_run(data_dir: Path, *, preset: str, n_procs: int, cells: int | None, iterations: int | None,
               solver_seconds: float | None, mesh_seconds: float | None, total_seconds: float | None = None,
               kind: str = "single", n_angles: int = 1, warm: bool = False, mesh_reused: bool = False,
               source: str = "run") -> dict | None:
    """Add one finished run. Returns the record, or None if the numbers are unusable."""
    try:
        cells, n_procs = int(cells or 0), int(n_procs)
        iterations = int(iterations or 0)
    except (TypeError, ValueError):
        return None
    if cells <= 0 or n_procs <= 0:
        return None
    rec: dict = {"time": time.time(), "source": source, "preset": str(preset), "n_procs": n_procs, "cells": cells,
                 "iterations": iterations, "kind": kind, "n_angles": int(n_angles), "warm": bool(warm),
                 "mesh_reused": bool(mesh_reused), "total_seconds": total_seconds}
    scale = 1.0 / core_scale(n_procs)  # measured at n ranks -> what 4 ranks would take
    if solver_seconds and iterations > 0:
        rec["solve_s_per_cell_iteration"] = float(solver_seconds) * scale / (cells * iterations)
    if mesh_seconds and not mesh_reused:
        rec["mesh_s_per_cell"] = float(mesh_seconds) * scale / cells
    if "solve_s_per_cell_iteration" not in rec and "mesh_s_per_cell" not in rec:
        return None
    recs = records(data_dir)[-(MAX_RECORDS - 1):] + [rec]
    p = _path(data_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"records": recs}, indent=1))
        os.replace(tmp, p)
    except OSError:
        return None
    return rec


def machine_speed(data_dir: Path) -> dict:
    """Solver and meshing speed of this computer at 4 ranks: medians of the last few records,
    or the conservative defaults when there are none."""
    recs = records(data_dir)
    solve = [r["solve_s_per_cell_iteration"] for r in recs if r.get("solve_s_per_cell_iteration")][-5:]
    mesh = [r["mesh_s_per_cell"] for r in recs if r.get("mesh_s_per_cell")][-5:]
    out = {"solve": statistics.median(solve) if solve else DEFAULT_SOLVE_S_PER_CELL_ITERATION,
           "mesh": statistics.median(mesh) if mesh else DEFAULT_MESH_S_PER_CELL,
           "basis": "measured" if solve else "default", "runs": len(recs)}
    quick = quick_cold_seconds(recs)
    if quick is not None:
        out["quick_seconds"] = quick
    return out


def quick_cold_seconds(recs: list[dict]) -> float | None:
    """The best recent cold Quick run of one angle on this computer (seconds), for the Detailed gate."""
    cold = [r["total_seconds"] for r in recs[-10:]
            if r.get("preset") == "quick" and r.get("kind") == "single" and not r.get("warm")
            and not r.get("mesh_reused") and r.get("total_seconds")]
    return min(cold) if cold else None


def estimate(data_dir: Path, preset: Preset | str, n_procs: int, quick_cells: int | None = None,
             n_angles: int = 1) -> dict:
    """Seconds (with the safety margin) and a plain-language text for one run."""
    preset = Preset(preset)
    sp = machine_speed(data_dir)
    base = quick_cells if quick_cells and quick_cells > 0 else PRESETS[Preset.QUICK].target_cells
    cells = base * CELLS_VS_QUICK.get(preset, 1.0)
    scale = core_scale(n_procs)
    mesh = cells * sp["mesh"] * scale
    per_angle = EXPECTED_ITERATIONS.get(preset, 300) * cells * sp["solve"] * scale
    seconds = SAFETY * (mesh + max(1, int(n_angles)) * per_angle)
    return {"preset": preset.value, "seconds": round(seconds), "text": duration_text(seconds), "basis": sp["basis"],
            "cells": int(round(cells, -3)), "memory_gb": PEAK_MEMORY_GB.get(preset)}


def duration_text(seconds: float) -> str:
    if seconds < 50:
        return "under a minute"
    m = math.ceil(seconds / 60)
    if m >= 90:
        h = m / 60
        return f"about {h:.1f} hours".replace(".0 hours", " hours")
    return f"about {m} minute{'s' if m != 1 else ''}"


def detailed_allowed(data_dir: Path, ram_bytes: int) -> bool:
    """Detailed (about 5x the Quick cells, 6-7 GB) is offered only on a computer with at least
    24 GB for Linux that has run a cold Quick case in under 90 s."""
    if ram_bytes < DETAILED_MIN_RAM_GB * 2**30:
        return False
    quick = quick_cold_seconds(records(data_dir))
    return quick is not None and quick < DETAILED_MAX_QUICK_SECONDS


def available_presets(data_dir: Path, ram_bytes: int) -> list[Preset]:
    out = [Preset.QUICK, Preset.STANDARD]
    if detailed_allowed(data_dir, ram_bytes):
        out.append(Preset.DETAILED)
    return out
