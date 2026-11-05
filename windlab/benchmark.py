"""``windlab benchmark``: time the standard Quick run on this computer.

Runs the NACA 0012 wing (chord 0.3 m, span 0.6 m) at 20 m/s and 10 degrees on the Quick
preset, by default on the ranks ``windlab start`` would use (cores minus one, at most 8,
:func:`windlab.machine.solver_cores`), and reports stage timings, peak
memory, cell count, iterations and CL/CD - as JSON (for setting the presets) and as a
short text summary. The presets are tuned from its numbers on the Windows test machines.
"""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from windlab import __version__, disclaimer, machine, speed

CASE = {"code": "0012", "chord": 0.3, "span": 0.6, "airspeed": 20.0, "alpha_deg": 10.0, "preset": "quick"}


def default_np() -> int:
    """The number of ranks ``windlab start`` would use on this computer."""
    return machine.solver_cores()


def run_benchmark(data_dir: Path, n_procs: int, alpha_deg: float = CASE["alpha_deg"], keep: bool = False,
                  runner: Callable | None = None, out: Callable[[str], None] = print) -> dict:
    from windlab import runner as runner_mod
    from windlab.case_builder import FlowConditions, GeometryInput, Preset, ReferenceValues, build_case
    from windlab.geometry.naca import naca_wing

    run = runner or runner_mod.run_case
    work = Path(tempfile.mkdtemp(prefix="benchmark-", dir=Path(data_dir)))
    w = naca_wing(CASE["code"], chord=CASE["chord"], span=CASE["span"])
    stl = work / "wing.stl"
    w.write_stl(stl)
    g = GeometryInput(stl_path=stl, bounds_min=tuple(w.vertices.min(0)), bounds_max=tuple(w.vertices.max(0)))
    ref = ReferenceValues(area=w.reference_area, length=w.mean_aero_chord, moment_point=tuple(w.quarter_chord_point))
    case = build_case(g, FlowConditions(airspeed=CASE["airspeed"], alpha_deg=alpha_deg), ref, preset=Preset.QUICK,
                      n_procs=n_procs, runs_root=work)

    def progress(kind: str, data: dict) -> None:
        if kind == "stage":
            out(f"  {time.strftime('%H:%M:%S')}  {data['stage']} ...")
        elif kind == "iteration":
            out(f"  {time.strftime('%H:%M:%S')}    iteration {data['iteration']}  CL {data['Cl']:.4f}  "
                f"CD {data['Cd']:.5f}")

    out(f"WindLab {__version__} benchmark: NACA {CASE['code']} wing, {CASE['airspeed']:g} m/s, {alpha_deg:g} deg, "
        f"Quick preset, {case.params['n_procs']} cores")
    t0 = time.monotonic()
    try:
        rep = run(case.path, progress=progress)
        wall = time.monotonic() - t0
        r = rep.to_dict() if hasattr(rep, "to_dict") else dict(rep)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    c = r.get("coefficients", {})
    stages = {s["name"]: round(s["seconds"], 2) for s in r.get("stages", [])}
    mesh_s = sum(v for k, v in stages.items() if k not in ("potentialFoam", "simpleFoam"))
    result = {
        "windlab_version": __version__,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": socket.gethostname(),
        "machine": machine.summary(),
        "openfoam": _foam_version(),
        "case": {**CASE, "alpha_deg": alpha_deg, "n_procs": case.params["n_procs"],
                 "max_iterations": case.params["max_iterations"]},
        "cells": r.get("mesh", {}).get("cells"),
        "mesh_ok": r.get("mesh", {}).get("mesh_ok"),
        "iterations": r.get("iterations"),
        "converged": r.get("converged"),
        "cl": c.get("cl"),
        "cd": c.get("cd"),
        "cm": c.get("cm"),
        "stage_seconds": stages,
        "mesh_seconds": round(mesh_s, 1),
        "solver_seconds": round(stages.get("simpleFoam", 0.0), 1),
        "seconds_per_iteration": round(stages.get("simpleFoam", 0.0) / r["iterations"], 4) if r.get("iterations")
        else None,
        "total_seconds": round(wall, 1),
        "peak_memory_mb": round(r.get("peak_rss_mb", 0.0)),
        "label": "uncalibrated prototype - not yet validated",
    }
    # the benchmark also teaches the app how fast this computer is (time estimates before a run)
    speed.record_run(Path(data_dir), preset="quick", n_procs=result["case"]["n_procs"], cells=result["cells"],
                     iterations=result["iterations"], solver_seconds=result["solver_seconds"],
                     mesh_seconds=result["mesh_seconds"], total_seconds=result["total_seconds"], source="benchmark")
    return result


def _foam_version() -> str | None:
    try:
        from windlab.runner import foam_env

        return foam_env().get("WM_PROJECT_VERSION")
    except Exception:  # noqa: BLE001
        return None


def text_summary(b: dict) -> str:
    m = b["machine"]
    lines = [
        f"WindLab benchmark, {b['date']} on {b['host']}",
        f"  computer : {m['cpu']}, {m['physical_cores']} physical / {m['logical_cores']} logical cores, "
        f"{m['ram_gb']} GB for Linux, {m['os']}{' (WSL2)' if m['wsl'] else ''}",
        f"  case     : NACA {b['case']['code']} wing, {b['case']['airspeed']:g} m/s, {b['case']['alpha_deg']:g} deg, "
        f"Quick, {b['case']['n_procs']} cores, OpenFOAM {b['openfoam']}",
        f"  mesh     : {b['cells'] or 0:,d} cells in {b['mesh_seconds']:.0f} s "
        f"(snappyHexMesh {b['stage_seconds'].get('snappyHexMesh', 0):.0f} s)",
        f"  solver   : {b['iterations']} iterations in {b['solver_seconds']:.0f} s "
        f"({(b['seconds_per_iteration'] or 0) * 1000:.0f} ms each), "
        f"{'settled' if b['converged'] else 'did NOT settle'}",
        f"  total    : {b['total_seconds']:.0f} s, peak memory {b['peak_memory_mb'] / 1024:.1f} GB",
        f"  result   : CL {b['cl']:.4f}, CD {b['cd']:.5f}  ({b['label']})" if b.get("cl") is not None
        else "  result   : no coefficients",
        f"  {disclaimer.FOOTER_ASCII}",
    ]
    return "\n".join(lines)


def default_out_path(b: dict) -> Path:
    day = b["date"][:10]
    host = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in b["host"])[:40] or "host"
    return Path.cwd() / f"windlab-benchmark-{host}-{day}-np{b['case']['n_procs']}.json"


def save(b: dict, path: Path) -> tuple[Path, Path]:
    path = Path(path)
    path.write_text(json.dumps(b, indent=2) + "\n")
    txt = path.with_suffix(".txt")
    txt.write_text(text_summary(b) + "\n")
    return path, txt
