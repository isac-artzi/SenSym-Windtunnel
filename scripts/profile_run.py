#!/usr/bin/env python3
"""Profile one WindLab run stage by stage (task 010, step 2).

Builds the app's default case (NACA 0012 wing, chord 0.3 m, span 0.6 m, 20 m/s) with the
app's own case builder, runs it through the real pipeline and records:

* wall-clock seconds of every stage (blockMesh, surfaceFeatureExtract, decomposePar,
  snappyHexMesh, checkMesh, potentialFoam, simpleFoam);
* the three snappyHexMesh phases (refine / snap / layers) from its log;
* simpleFoam: iterations, seconds per iteration (first 50, overall, last 100) from the
  ExecutionTime lines of its log, and the time the stop rule needed;
* the post-processing WindLab does afterwards: 3D surface extraction, flow slice
  decoding, streamlines;
* CL, CD (with the pressure/viscous split), cells, peak memory.

    python scripts/profile_run.py --np 4 --alpha 10                 # one cold run
    python scripts/profile_run.py --np 4 --angles -4 0 4 8 12 16    # a sweep
    python scripts/profile_run.py --np 4 --alpha 10 --label after-gamg --out validation/perf

With ``--data DIR`` the run goes through the app's RunManager (mesh cache, warm start),
so a second run on the same shape measures what a student sees.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from windlab import machine  # noqa: E402
from windlab.app import slice as flow_slice  # noqa: E402
from windlab.app import surface  # noqa: E402
from windlab.case_builder import (  # noqa: E402
    DEFAULT_RUNS_ROOT,
    FlowConditions,
    GeometryInput,
    Preset,
    ReferenceValues,
    build_case,
    build_sweep_case,
)
from windlab.geometry.naca import naca_wing  # noqa: E402
from windlab.runner import RunError, run_case  # noqa: E402

CODE, CHORD, SPAN = "0012", 0.3, 0.6


def parse_snappy_phases(log: Path) -> dict:
    text = log.read_text(errors="replace") if log.is_file() else ""
    out = {}
    for key, pat in (("refine", r"Mesh refined in = ([\d.]+) s"), ("snap", r"Mesh snapped in = ([\d.]+) s"),
                     ("layers", r"Layers added in = ([\d.]+) s"), ("total", r"Finished meshing in = ([\d.]+) s")):
        m = re.findall(pat, text)
        if m:
            out[key] = float(m[-1])
    return out


def parse_solver_log(log: Path) -> dict:
    """Seconds per iteration from the ExecutionTime lines (cumulative CPU-ish time of rank 0)."""
    text = log.read_text(errors="replace") if log.is_file() else ""
    ex = [float(x) for x in re.findall(r"^ExecutionTime = ([\d.]+) s", text, re.M)]
    clock = [float(x) for x in re.findall(r"ClockTime = ([\d.]+) s", text)]
    if len(ex) < 2:
        return {}
    d = [b - a for a, b in zip(ex, ex[1:], strict=False)]
    n = len(d)
    return {
        "iterations_logged": len(ex),
        "first_50_s_per_it": sum(d[:50]) / min(50, n),
        "mean_s_per_it": sum(d) / n,
        "last_100_s_per_it": sum(d[-100:]) / min(100, n),
        "exec_total_s": ex[-1],
        "clock_total_s": clock[-1] if clock else None,
        "p_iterations_mean": _mean_count(text, r"GAMG:  Solving for p, .* No Iterations (\d+)"),
        "ux_iterations_mean": _mean_count(text, r"Solving for Ux, .* No Iterations (\d+)"),
    }


def _mean_count(text: str, pat: str) -> float | None:
    m = re.findall(pat, text)
    return sum(map(int, m)) / len(m) if m else None


def timed(fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    return r, time.perf_counter() - t0


def post_process(case: Path, airspeed: float) -> dict:
    out = {}
    try:
        blob, out["surface_s"] = timed(surface.pressure_surface, case, airspeed)
        out["surface_bytes"] = len(blob)
    except Exception as e:  # noqa: BLE001
        out["surface_error"] = str(e)
    try:
        data, out["slice_read_s"] = timed(flow_slice.read_slice, case)
        blob, out["slice_encode_s"] = timed(flow_slice.flow_slice, case, airspeed, data=data)
        d = flow_slice.decode(blob)
        meta = json.loads((case / "windlab_case.json").read_text())
        _, out["streamlines_s"] = timed(flow_slice.slice_info, meta,
                                        [d["x_min"], d["x_max"], d["z_min"], d["z_max"]], d["y"],
                                        data=data, airspeed=airspeed)
        out["slice_bytes"] = len(blob)
    except Exception as e:  # noqa: BLE001
        out["slice_error"] = str(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha", type=float, default=10.0)
    ap.add_argument("--angles", type=float, nargs="+", default=None, help="a sweep instead of a single run")
    ap.add_argument("--speed", type=float, default=20.0)
    ap.add_argument("--np", type=int, default=4, dest="n_procs")
    ap.add_argument("--preset", default="quick", choices=[p.value for p in Preset if p != Preset.CHECK])
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS_ROOT)
    ap.add_argument("--data", type=Path, default=None, help="use the app's RunManager with this data folder")
    ap.add_argument("--geometry-id", default=None, help="with --data: reuse this stored shape")
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--out", type=Path, default=REPO / "validation" / "perf")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--mesh-only", action="store_true", help="stop after meshing (snappyHexMesh experiments)")
    ap.add_argument("--patch", nargs=3, action="append", default=[], metavar=("FILE", "OLD", "NEW"),
                    help="after building the case, replace OLD by NEW in case FILE (experiments; repeatable)")
    args = ap.parse_args()

    wall0 = time.perf_counter()
    stage_t: dict[str, float] = {}
    events: list[dict] = []
    iter_marks: list[tuple[float, int]] = []
    t_start = time.perf_counter()

    def progress(kind: str, data: dict) -> None:
        now = time.perf_counter() - t_start
        if kind == "stage":
            print(f"[{now:7.1f}] {data['stage']} ...", flush=True)
        elif kind == "stage_done":
            stage_t[data["name"]] = stage_t.get(data["name"], 0.0) + data["seconds"]
            print(f"[{now:7.1f}]   {data['name']} done in {data['seconds']:.1f} s, peak {data['peak_rss_mb']:.0f} MB",
                  flush=True)
        elif kind == "iteration":
            iter_marks.append((now, int(data["iteration"])))
            print(f"[{now:7.1f}]   iter {data['iteration']:5d}  CL {data['Cl']:+.4f}  CD {data['Cd']:.5f}", flush=True)
        elif kind == "converged":
            print(f"[{now:7.1f}]   settled at iteration {data['iteration']}", flush=True)
            events.append({"converged": data["iteration"], "t": now})
        elif kind == "mesh":
            print(f"[{now:7.1f}]   mesh: {data.get('cells')} cells, OK = {data.get('mesh_ok')}", flush=True)
        elif kind in ("point", "point_done", "mesh_reused", "warm_start"):
            print(f"[{now:7.1f}]   {kind}: {data}", flush=True)
            events.append({kind: data, "t": now})

    result: dict = {"label": args.label, "date": time.strftime("%Y-%m-%d %H:%M:%S"), "host": socket.gethostname(),
                    "machine": machine.summary(), "n_procs": args.n_procs, "preset": args.preset,
                    "airspeed": args.speed, "alpha": args.alpha, "angles": args.angles}

    if args.data is not None:
        rep, case_dir, extra = run_via_app(args, progress)
        result.update(extra)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="windlab_profile_"))
        wing = naca_wing(CODE, chord=CHORD, span=SPAN)
        stl = tmp / "wing.stl"
        wing.write_stl(stl)
        lo, hi = wing.bounds
        geom = GeometryInput(stl_path=stl, bounds_min=tuple(lo), bounds_max=tuple(hi))
        ref = ReferenceValues(area=wing.reference_area, length=wing.mean_aero_chord,
                              moment_point=wing.quarter_chord_point)
        if args.angles:
            case = build_sweep_case(geom, args.speed, args.angles, ref, preset=args.preset, n_procs=args.n_procs,
                                    runs_root=args.runs)
        else:
            case = build_case(geom, FlowConditions(airspeed=args.speed, alpha_deg=args.alpha), ref,
                              preset=args.preset, n_procs=args.n_procs, runs_root=args.runs)
        case_dir = case.path
        print(f"case: {case_dir}")
        for rel, old, new in args.patch:
            old, new = old.replace("\\n", "\n"), new.replace("\\n", "\n")
            f = case_dir / rel
            text = f.read_text()
            if text.count(old) != 1:
                print(f"patch: {old!r} occurs {text.count(old)} times in {rel}, expected once")
                return 2
            f.write_text(text.replace(old, new))
            print(f"patched {rel}: {old!r} -> {new!r}")
        try:
            rep = run_case(case_dir, progress=progress, on_point=(lambda i, r: None) if args.angles else None,
                           mesh_only=args.mesh_only)
        except RunError as e:
            print(f"RUN FAILED in {e.stage}: {e}\n    log: {e.log}")
            return 1
    solver_wall = time.perf_counter() - wall0
    result["patches"] = args.patch

    post = post_process(case_dir, args.speed) if not (args.angles or args.mesh_only) else {}
    total_wall = time.perf_counter() - wall0
    r = rep.to_dict()
    c = r.get("coefficients", {})
    result.update({
        "case": str(case_dir),
        "stages": [{"name": s["name"], "seconds": round(s["seconds"], 2), "peak_mb": round(s["peak_rss_mb"])}
                   for s in r["stages"]],
        "stage_seconds": {k: round(v, 2) for k, v in stage_t.items()},
        "snappy_phases": parse_snappy_phases(case_dir / "log.snappyHexMesh"),
        "solver_log": parse_solver_log(case_dir / "log.simpleFoam") if not args.angles else
        {f"p{i}": parse_solver_log(case_dir / f"log.simpleFoam.{i}") for i in range(len(args.angles))},
        "iterations": r.get("iterations"),
        "converged": r.get("converged"),
        "cells": r.get("mesh", {}).get("cells"),
        "mesh_ok": r.get("mesh", {}).get("mesh_ok"),
        "layers": r.get("mesh", {}).get("layers"),
        "max_non_orthogonality": r.get("mesh", {}).get("max_non_orthogonality"),
        "cl": c.get("cl"), "cd": c.get("cd"), "cm": c.get("cm"),
        "cd_pressure": c.get("cd_pressure"), "cd_viscous": c.get("cd_viscous"),
        "cl_pm": c.get("cl_pm"), "cd_pm": c.get("cd_pm"),
        "points": [{k: p.get(k) for k in ("alpha_deg", "status", "iterations", "converged", "seconds")}
                   | {"cl": p.get("coefficients", {}).get("cl"), "cd": p.get("coefficients", {}).get("cd")}
                   for p in r.get("points", [])],
        "y_plus": r.get("y_plus"),
        "events": events,
        "post": post,
        "run_case_wall_s": round(solver_wall, 1),
        "total_wall_s": round(total_wall, 1),
        "peak_memory_mb": round(r.get("peak_rss_mb", 0)),
        "notes": r.get("notes"),
    })
    args.out.mkdir(parents=True, exist_ok=True)
    name = args.label + (f"-sweep{len(args.angles)}" if args.angles else f"-a{args.alpha:g}") + f"-np{args.n_procs}"
    if args.mesh_only:
        name += "-mesh"
    out = args.out / f"{name}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(summary(result))
    print(f"\nwrote {out}")
    return 0


def run_via_app(args, progress):
    """Run through the app's RunManager so mesh reuse and warm starts are exercised."""
    from windlab.app.geometry_store import GeometryStore
    from windlab.app.runs import RunManager

    data = args.data
    geometry = GeometryStore(data / "geometry")
    gid = args.geometry_id
    if gid is None:
        gid = geometry.add_naca(CODE, CHORD, SPAN)["id"]
    seen: dict = {}

    def runner(case_dir, cache=None, **kw):
        seen["case"] = Path(case_dir)
        p = kw.pop("progress", None)

        def both(kind, d):
            if p:
                p(kind, d)
            progress(kind, d)

        rep = run_case(case_dir, progress=both, cache=cache, **kw)
        seen["rep"] = rep
        return rep

    mgr = RunManager(data / "runs", geometry, n_procs=args.n_procs, runner=runner, data_root=data)
    if args.angles:
        st = mgr.submit_sweep(gid, args.speed, args.angles, args.preset)
    else:
        st = mgr.submit(gid, args.speed, args.alpha, args.preset)
    t0 = time.perf_counter()
    while st.status in ("queued", "running"):
        time.sleep(0.5)
    app_wall = time.perf_counter() - t0
    mgr.stop()
    if st.status != "done":
        print("RUN FAILED:", st.error)
        sys.exit(1)
    extra = {"geometry_id": gid, "app_wall_s": round(app_wall, 1), "app_stage_times": st.stage_times,
             "app_result": st.result, "warnings": st.warnings}
    return seen["rep"], seen["case"], extra


def summary(r: dict) -> str:
    L = [f"\n{r['label']}: {r['n_procs']} ranks, preset {r['preset']}, "
         + (f"sweep {r['angles']}" if r["angles"] else f"alpha {r['alpha']:g}")]
    L.append("| stage | seconds |")
    L.append("|:--|---:|")
    for s in r["stages"]:
        L.append(f"| {s['name']} | {s['seconds']:.1f} |")
    for k, v in r.get("snappy_phases", {}).items():
        L.append(f"| snappyHexMesh: {k} | {v:.1f} |")
    for k, v in r.get("post", {}).items():
        if isinstance(v, float):
            L.append(f"| post: {k} | {v:.2f} |")
    L.append(f"| run_case wall | {r['run_case_wall_s']:.1f} |")
    L.append(f"| total wall | {r['total_wall_s']:.1f} |")
    sl = r.get("solver_log") or {}
    if "mean_s_per_it" in sl:
        L.append(f"\nsimpleFoam: {r['iterations']} iterations, {sl['mean_s_per_it'] * 1000:.0f} ms/it mean "
                 f"(first 50: {sl['first_50_s_per_it'] * 1000:.0f}, last 100: {sl['last_100_s_per_it'] * 1000:.0f}), "
                 f"p GAMG {sl.get('p_iterations_mean', 0):.1f} cycles/it")
    L.append(f"cells {r['cells']}, CL {r['cl']}, CD {r['cd']} (p {r['cd_pressure']}, v {r['cd_viscous']}), "
             f"converged {r['converged']}, peak {r['peak_memory_mb']} MB")
    lay = (r.get("layers") or {}).get("body") or {}
    L.append(f"mesh OK {r.get('mesh_ok')}, max non-orthogonality {r.get('max_non_orthogonality')}, layers "
             f"{lay.get('mean_layers')} of {lay.get('target_layers')} ({lay.get('thickness_pct')} % thickness)")
    for p in r.get("points", []):
        L.append(f"  point {p['alpha_deg']:g}: {p['status']} {p['iterations']} it {p['seconds']:.0f} s "
                 f"CL {p['cl']} CD {p['cd']}")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
