#!/usr/bin/env python3
"""Milestone 1 check: NACA 0012 wing (c = 0.3 m, b = 0.6 m) at 20 m/s, alpha 0 and 10 deg.

Builds the geometry and the cases, runs blockMesh -> snappyHexMesh -> simpleFoam,
and prints wall-clock time per stage, peak memory, cell count, CL and CD.

    python scripts/run_naca.py                 # both angles, Quick preset, 4 ranks
    python scripts/run_naca.py --np 6 --alpha 10
    python scripts/run_naca.py --mesh-only     # stop after meshing (preset tuning)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from windlab.case_builder import (  # noqa: E402
    DEFAULT_RUNS_ROOT,
    FlowConditions,
    GeometryInput,
    Preset,
    ReferenceValues,
    build_case,
)
from windlab.geometry.naca import naca_wing  # noqa: E402
from windlab.runner import RunError, run_case  # noqa: E402

LABEL = "UNCALIBRATED PROTOTYPE - NOT YET VALIDATED"


def progress(kind: str, data: dict) -> None:
    stamp = time.strftime("%H:%M:%S")
    if kind == "stage":
        print(f"[{stamp}] {data['stage']} ...", flush=True)
    elif kind == "stage_done":
        print(f"[{stamp}]   done in {data['seconds']:.1f} s, peak {data['peak_rss_mb']:.0f} MB", flush=True)
    elif kind == "mesh":
        print(f"[{stamp}]   mesh: {data.get('cells', '?')} cells, mesh OK = {data.get('mesh_ok')}", flush=True)
    elif kind == "iteration":
        print(f"[{stamp}]   iter {data['iteration']:5d}  CL {data['Cl']:+.4f}  CD {data['Cd']:.5f}", flush=True)
    elif kind == "converged":
        print(f"[{stamp}]   CL/CD settled at iteration {data['iteration']}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha", type=float, nargs="+", default=[0.0, 10.0])
    ap.add_argument("--speed", type=float, default=20.0)
    ap.add_argument("--np", type=int, default=4, dest="n_procs")
    ap.add_argument("--preset", default="quick", choices=[p.value for p in Preset])
    ap.add_argument("--mesh-only", action="store_true")
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS_ROOT)
    args = ap.parse_args()

    wing = naca_wing("0012", chord=0.3, span=0.6)
    tmp = Path(tempfile.mkdtemp(prefix="windlab_geom_"))
    stl = tmp / "naca0012.stl"
    wing.write_stl(stl)
    lo, hi = wing.bounds
    geom = GeometryInput(stl_path=stl, bounds_min=tuple(lo), bounds_max=tuple(hi))
    ref = ReferenceValues(area=wing.reference_area, length=wing.mean_aero_chord,
                          moment_point=wing.quarter_chord_point)

    reports = []
    for alpha in args.alpha:
        case = build_case(geom, FlowConditions(airspeed=args.speed, alpha_deg=alpha), ref,
                          preset=args.preset, n_procs=args.n_procs, runs_root=args.runs)
        p = case.params
        print(f"\n=== NACA 0012, alpha {alpha:g} deg, {args.speed:g} m/s, Re {p['reynolds']:.3g}, "
              f"preset {p['preset']}, {p['n_procs']} ranks")
        print(f"    case: {case.path}")
        try:
            rep = run_case(case.path, progress=progress, mesh_only=args.mesh_only)
        except RunError as e:
            print(f"RUN FAILED in {e.stage}: {e}\n    log: {e.log}")
            return 1
        reports.append((alpha, rep))

    print(f"\n{'=' * 78}\nNACA 0012  c = 0.3 m  b = 0.6 m  S = {wing.reference_area:g} m^2  "
          f"V = {args.speed:g} m/s  ({LABEL})\n")
    stages = [s.name for s in reports[0][1].stages]
    def row(label, cells):
        print(f"{label:26s}" + "".join(f"{c:>22s}" for c in cells))

    row("stage  (wall clock, peak RSS)", [f"alpha {a:g} deg" for a, _ in reports])
    for name in stages:
        cells = []
        for _, r in reports:
            s = next((s for s in r.stages if s.name == name), None)
            cells.append(f"{s.seconds:6.1f} s {s.peak_rss_mb:6.0f} MB" if s else "-")
        row(name, cells)
    row("total", [f"{r.total_seconds:6.1f} s {r.peak_rss_mb:6.0f} MB" for _, r in reports])
    row("cells", [f"{r.mesh.get('cells', 0):,d}" for _, r in reports])
    row("max non-orthogonality", [f"{r.mesh.get('max_non_orthogonality', 0):.1f} deg" for _, r in reports])
    row("boundary layers (avg/5)", [
        " ".join(f"{v['mean_layers']:.2f} ({v['thickness_pct']:.0f} %)" for v in r.mesh.get("layers", {}).values())
        for _, r in reports])
    if not args.mesh_only:
        row("iterations", [f"{r.iterations}" + ("" if r.converged else " (cap)") for _, r in reports])
        for key, name in (("cl", "CL"), ("cd", "CD"), ("cm", "Cm (c/4)")):
            row(name, [f"{r.coefficients[key]:+.4f} +/- {r.coefficients[key + '_pm']:.4f}" for _, r in reports])
        row("L/D", [f"{r.coefficients.get('l_over_d', 0):.2f}" for _, r in reports])
        row("y+ min / mean / max", [
            " ".join(f"{y['min']:.0f} / {y['average']:.0f} / {y['max']:.0f}" for y in r.y_plus.values())
            for _, r in reports])
        row("max final residual", [f"{max(r.residuals.values()):.1e}" if r.residuals else "-" for _, r in reports])
        for a, r in reports:
            for n in r.notes:
                print(f"note (alpha {a:g}): {n}")
    out = args.runs / f"naca0012_summary_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps([{"alpha": a, **r.to_dict()} for a, r in reports], indent=2))
    print(f"\nsummary: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
