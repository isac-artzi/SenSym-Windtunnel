#!/usr/bin/env python3
"""Robustness of the upload -> check -> Quick mesh -> solve chain on student-like parts (task 011, step 2).

The feasibility study's biggest risk is automatic meshing of arbitrary geometry. This script
builds a corpus of ten parts the way SolidWorks would export them (STEP and STL, in
millimetres), pushes each through the app's own routes and run manager (Quick preset, 20 m/s,
0 degrees, 4 ranks) and records pass/fail, cells, mesh quality, times, CD and the failure mode.

    python scripts/robustness_corpus.py --build-only          # just write the parts
    python scripts/robustness_corpus.py --np 4                # build, upload, run everything (about 15 min)
    python scripts/robustness_corpus.py --only rocket plate-2mm   # a subset (the other rows are kept)
    python scripts/robustness_corpus.py --report-only             # rewrite robustness.md from the JSON

Outputs: ``validation/robustness/parts/<name>.step`` (committed; the STL twins are written next to
them but not committed), ``validation/robustness/results.json`` and ``validation/robustness.md``.
The report's before/after tables read ``results_task012.json`` (no band) and ``results_task013.json``
(the band without its cell budget). Needs OpenCASCADE (``pip install ".[step]"``) to build the parts
and OpenFOAM to run them. The STEP files are rewritten with a new timestamp only; ``git checkout``
them afterwards.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from windlab import __version__, machine  # noqa: E402
from windlab.geometry.naca import naca4_section  # noqa: E402

OUT = REPO / "validation" / "robustness"
PARTS = OUT / "parts"
AIRSPEED, ALPHA, PRESET = 20.0, 0.0, "quick"


# ----------------------------------------------------------------------------- the parts (mm)


def _airfoil_points(chord: float, code: str = "0012", n: int = 40) -> list[tuple[float, float]]:
    pts = naca4_section(code, n_per_side=n) * chord
    # the generator's outline starts and ends at the trailing edge: drop the duplicate point
    return [(float(x), float(z)) for x, z in pts][:-1]


def _bracket(g):
    """An L of 6 mm plate with three through holes, a side hole and rounded outer edges."""
    bracket = g.fuse(g.box(120, 80, 6), g.box(6, 80, 60))
    for yc in (20, 40, 60):
        bracket = g.cut(bracket, g.cylinder(5, 20, "z", at=(70, yc, -5)))
    bracket = g.cut(bracket, g.cylinder(8, 20, "x", at=(-5, 40, 35)))
    return g.fillet_all(bracket, 2.0)


def _car(g):
    """Lower box + cabin with rounded edges, on four short wheel stubs."""
    lower = g.fillet_all(g.box(400, 180, 70, at=(0, -90, 30)), 15.0)
    cabin = g.fillet_all(g.box(200, 150, 70, at=(110, -75, 95)), 20.0)
    car = g.fuse(lower, cabin)
    for x in (70, 320):
        for y in (-90, 60):
            car = g.fuse(car, g.cylinder(30, 30, "y", at=(x, y, 30)))
    return car


def _rocket(g):
    """Conical nose + tube + four 2 mm fins (each fin's root reaches into the tube, as a CAD user would draw it,
    so the boolean union gives one solid)."""
    body = g.revolve_polygon([(0, 0), (60, 12), (120, 20), (420, 20), (420, 0)])
    fin = g.extrude_polygon([(330, 15), (420, 15), (420, 60), (390, 60)], 2.0, y0=-1.0)
    rocket = body
    for k in range(4):
        rocket = g.fuse(rocket, g.rotate(fin, "x", 90 * k))
    return rocket


def _drone_arm(g):
    """A tube with a motor mount (with a bolt hole) at one end and a clamp plate at the other."""
    drone = g.fuse(g.fuse(g.cylinder(8, 220, "x"), g.cylinder(16, 8, "z", at=(205, 0, -4))),
                   g.box(30, 24, 4, at=(-10, -12, -2)))
    return g.cut(drone, g.cylinder(3, 20, "z", at=(205, 0, -10)))


def _wing_winglet(g):
    """NACA 2412 wing, chord 150 mm, span 400 mm, with a 70 mm winglet canted 75 degrees up at one tip."""
    wing = g.extrude_spline(_airfoil_points(150, "2412"), 400, y0=-200)
    winglet = g.extrude_spline([(0.4 * x + 90, 0.4 * z) for x, z in _airfoil_points(150, "2412")], 70, y0=-5)
    winglet = g.translate(g.rotate(winglet, "x", -75, about=(0, 0, 0)), 0, 200, 0)
    return g.fuse(wing, winglet)


def _beam(g):
    beam = g.fuse(g.box(2000, 200, 100, at=(0, -100, 0)), g.box(2000, 300, 20, at=(0, -150, 100)))
    return g.fillet_all(beam, 8.0)


# name -> (builder, bodies mode for the upload, one-line description); sizes in mm like a SolidWorks export
CORPUS: dict[str, tuple] = {
    "bracket": (_bracket, "largest", "L-bracket 120 x 80 x 60 mm, 6 mm plate, four holes, 2 mm fillets"),
    "car": (_car, "largest", "car body 400 mm long with a rounded cabin and four wheels"),
    "rocket": (_rocket, "largest", "rocket 420 mm long, 40 mm diameter, four 2 mm thick fins"),
    "drone-arm": (_drone_arm, "largest", "drone arm: 16 mm tube, 220 mm long, motor mount and clamp plate"),
    "wing-winglet": (_wing_winglet, "largest", "NACA 2412 wing, chord 150 mm, span 400 mm, with a 70 mm winglet"),
    "plate-2mm": (lambda g: g.box(200, 100, 2, at=(0, -50, -1)), "largest", "flat plate 200 x 100 x 2 mm, edge-on"),
    "reentrant": (lambda g: g.extrude_polygon([(0, 0), (100, 0), (100, 40), (40, 40), (40, 100), (0, 100)], 80,
                                              y0=-40),
                  "largest", "L-section block 100 x 100 x 80 mm with a sharp 270 degree inside corner"),
    "assembly": (lambda g: g.compound(g.cylinder(25, 150, "x"), g.box(60, 40, 40, at=(165, -20, -20))), "all",
                 "two separate bodies 15 mm apart (assembly export), both in the tunnel"),
    "beam-2m": (_beam, "largest", "2 m long beam with a flange, 8 mm fillets"),
    "knob-30mm": (lambda g: g.fuse(g.sphere(12, at=(12, 0, 0)), g.cylinder(4, 10, "x", at=(20, 0, 0))), "largest",
                  "30 mm knob: 24 mm sphere on an 8 mm stem"),
}


def build_parts(g, names=None) -> dict[str, dict]:
    """name -> {"shape": OCP shape (mm), "bodies": "largest"|"all", "about": str}. ``g`` is tests.stepgen."""
    parts: dict[str, dict] = {}
    for name in names or CORPUS:
        build, bodies, about = CORPUS[name]
        print(f"building {name} ...", flush=True)
        parts[name] = {"shape": build(g), "bodies": bodies, "about": about}
    return parts


def write_parts(parts: dict[str, dict]) -> None:
    from tests import stepgen as g

    PARTS.mkdir(parents=True, exist_ok=True)
    for name, p in parts.items():
        g.write_step(PARTS / f"{name}.step", p["shape"], "mm")
        g.write_stl(PARTS / f"{name}.stl", p["shape"], deflection=0.05)
        print(f"wrote {name}.step / .stl", flush=True)


# ----------------------------------------------------------------------------- the runs


def run_corpus(names: list[str], parts: dict[str, dict], n_procs: int, data: Path) -> list[dict]:
    from tests.asgi_client import make_client
    from windlab.app import Settings, create_app

    app = create_app(Settings(data_dir=data, n_procs=n_procs))
    client = make_client(app)
    rows = []
    try:
        for name in names:
            about, bodies = parts[name]["about"], parts[name]["bodies"]
            row: dict = {"part": name, "about": about, "bodies": bodies}
            print(f"\n=== {name}: {about}", flush=True)
            t0 = time.perf_counter()
            r = client.post("/api/geometry", params={"name": f"{name}.step", "bodies": bodies},
                            content=(PARTS / f"{name}.step").read_bytes())
            row["upload_seconds"] = round(time.perf_counter() - t0, 1)
            if r.status_code != 200:
                row.update(status="upload-failed", error=r.json().get("detail"))
                rows.append(row)
                print("  upload failed:", row["error"])
                continue
            g = r.json()
            thin = (g.get("thin_features") or {}).get(PRESET) or None
            row.update(geometry_id=g["geometry_id"], usable=g["usable"], units=g["units"], triangles=g["triangles"],
                       pieces=g["pieces"], size_m=[round(x, 4) for x in g["size_m"]], problems=g["problems"],
                       warnings=g["warnings"], source={k: g["source"][k] for k in ("solids", "faces", "fused",
                                                                                  "healed", "bodies_available")},
                       reference=g["reference"],
                       thin={k: thin.get(k) for k in ("thickness", "area_share", "cells_across", "refined_cell",
                                                      "rough", "notes", "extra_levels", "levels_wanted", "capped",
                                                      "added_cells", "full_added_cells", "cell_budget")}
                       if thin else None)
            print(f"  {g['triangles']} triangles, size {row['size_m']} m, usable {g['usable']}")
            # the STL twin must pass the same checks
            rs = client.post("/api/geometry", params={"name": f"{name}.stl", "units": "mm", "bodies": bodies},
                             content=(PARTS / f"{name}.stl").read_bytes())
            row["stl_twin"] = {"status": rs.status_code, "usable": rs.json().get("usable"),
                               "triangles": rs.json().get("triangles"), "problems": rs.json().get("problems")}
            if not g["usable"]:
                row.update(status="check-failed", error="; ".join(g["problems"]))
                rows.append(row)
                continue
            t0 = time.perf_counter()
            r = client.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": AIRSPEED,
                                               "alpha_deg": ALPHA, "preset": PRESET, "units_confirmed": True})
            if r.status_code != 202:
                row.update(status="submit-failed", error=r.json().get("detail"))
                rows.append(row)
                print("  submit failed:", row["error"])
                continue
            rid = r.json()["run_id"]
            s = None
            while True:
                s = client.get(f"/api/runs/{rid}").json()
                if s["status"] not in ("queued", "running"):
                    break
                time.sleep(2.0)
            row["run_seconds"] = round(time.perf_counter() - t0, 1)
            row["stage_seconds"] = {k: round(v, 1) for k, v in (s.get("stage_times") or {}).items()}
            row["status"] = s["status"]
            row["error"] = s.get("error")
            row["run_warnings"] = s.get("warnings") or []
            row["thin_refinement"] = (s.get("params") or {}).get("thin_refinement")
            case = app.fastapi.state.runs.root / rid
            rep = {}
            if (case / "windlab_report.json").is_file():
                rep = json.loads((case / "windlab_report.json").read_text())
            mesh = rep.get("mesh") or {}
            row["mesh"] = {k: mesh.get(k) for k in ("cells", "max_non_orthogonality", "max_skewness", "failed_checks",
                                                    "mesh_ok")}
            lay = (mesh.get("layers") or {}).get("body") or {}
            row["layers"] = {k: lay.get(k) for k in ("mean_layers", "target_layers", "thickness_pct")}
            row["failed_stage"] = s.get("stage") if s["status"] == "failed" else None
            row["log_tail"] = _log_tail(case, s.get("stage")) if s["status"] == "failed" else None
            res = s.get("result") or {}
            row["result"] = {k: res.get(k) for k in ("cl", "cd", "cm", "cl_pm", "cd_pm", "iterations", "converged",
                                                     "cells", "solver_seconds", "peak_memory_mb")}
            c = rep.get("coefficients") or {}
            row["result"]["cd_pressure"], row["result"]["cd_viscous"] = c.get("cd_pressure"), c.get("cd_viscous")
            row["y_plus"] = (rep.get("y_plus") or {}).get("body")
            row["reynolds"] = (s.get("params") or {}).get("reynolds")
            print(f"  {s['status']}: cells {row['mesh']['cells']}, CD {row['result']['cd']}, "
                  f"{row['run_seconds']} s" + (f", error: {s.get('error')}" if s.get("error") else ""))
            rows.append(row)
    finally:
        app.fastapi.state.runs.stop()
    return rows


def _log_tail(case: Path, stage: str | None, n: int = 15) -> list[str] | None:
    if not stage:
        return None
    for f in sorted(case.glob(f"log.{stage}*")):
        try:
            return f.read_text(errors="replace").splitlines()[-n:]
        except OSError:
            return None
    return None


# ----------------------------------------------------------------------------- the report


def _f(x, nd=4) -> str:
    return "-" if x is None else f"{x:.{nd}g}"


def render(data: dict) -> str:
    info, rows = data["info"], data["rows"]
    L = [
        "# Robustness on student-like parts (STEP and STL)",
        "",
        f"Generated by `scripts/robustness_corpus.py` on {info['date']} ({info['machine']}, {info['n_procs']} MPI "
        f"ranks, one run at a time, WindLab {info['version']}). Raw numbers: `robustness/results.json`; the parts: "
        "`robustness/parts/*.step` (built with OpenCASCADE the way SolidWorks exports them, in millimetres). "
        "**Uncalibrated prototype - not yet validated.**",
        "",
        f"Every part went through the app's own routes: upload (STEP, units read from the file) -> geometry checks -> "
        f"Quick mesh -> simpleFoam at {info['airspeed']:g} m/s and {info['alpha_deg']:g} degrees -> results, with the "
        "default reference values: the length along the wind, and the **frontal area** for a body or the **planform "
        "area** for a wing-like part, both measured from the surface (`windlab.geometry.reference`; the 'ref. area' "
        "column says which). The STL twin of every part was uploaded too and had to pass the same checks.",
        "",
        "## Results",
        "",
        "| part | about | STEP read | STL twin | tris | mesh | cells | non-orth. max | layers | time (upload / mesh / "
        "solve) | ref. area | CD | CL | settled | failure mode |",
        "|:--|:--|:--|:--|---:|:--|---:|---:|---:|:--|:--|---:|---:|:--|:--|",
    ]
    for r in rows:
        src = r.get("source") or {}
        read = ("ok" if r.get("usable") else "no") + (f" ({src.get('solids')} solids" + (", fused" if src.get("fused")
                                                                                             else "") + ")"
                                                      if src else "")
        twin = r.get("stl_twin") or {}
        twin_s = "ok" if twin.get("usable") else ("no" if twin else "-")
        st = r.get("status")
        mesh = r.get("mesh") or {}
        lay = r.get("layers") or {}
        res = r.get("result") or {}
        stage = r.get("stage_seconds") or {}
        mesh_s = sum(v for k, v in stage.items() if k in ("blockMesh", "surfaceFeatureExtract", "decomposePar",
                                                           "snappyHexMesh", "checkMesh", "meshReuse"))
        solve_s = stage.get("simpleFoam", 0) + stage.get("potentialFoam", 0)
        if st == "done":
            mesh_txt = "OK" if mesh.get("mesh_ok") else f"{mesh.get('failed_checks')} checks failed"
            fail = "-"
            if mesh.get("failed_checks"):
                fail = "mesh quality warning only"
        elif st in ("check-failed", "upload-failed"):
            mesh_txt, fail = "-", f"{st}: {r.get('error')}"
        else:
            mesh_txt = "failed" if r.get("failed_stage") in ("snappyHexMesh", "checkMesh") else "OK"
            fail = f"{st} in {r.get('failed_stage')}: {(r.get('error') or '')[:90]}"
        layers = f"{lay['thickness_pct']:.0f} %" if lay.get("thickness_pct") is not None else "-"
        ref = r.get("reference") or {}
        measured = "silhouette" if ref.get("area_method") == "silhouette" else "box"
        ref_txt = f"{ref.get('part_type', '?')}: {_f(ref.get('area'), 3)} m² ({measured})" if ref else "-"
        L.append(f"| {r['part']} | {r['about']} | {read} | {twin_s} | {r.get('triangles', '-')} | {mesh_txt} | "
                 f"{mesh.get('cells') or '-'} | {_f(mesh.get('max_non_orthogonality'), 3)} | {layers} | "
                 f"{r.get('upload_seconds', '-')} / {mesh_s:.0f} / {solve_s:.0f} s | {ref_txt} | {_f(res.get('cd'))} | "
                 f"{_f(res.get('cl'), 3)} | {'yes' if res.get('converged') else ('no' if st == 'done' else '-')} | "
                 f"{fail} |")
    n_ok = sum(1 for r in rows if r.get("status") == "done")
    L += ["", f"**{n_ok} of {len(rows)} parts meshed and solved.** Times are wall-clock seconds in the dev container "
             "on 4 ranks; the Windows laptop is roughly 2.5x slower (validation/performance.md).", ""]
    before = {r["part"]: r for r in (data.get("before") or {}).get("rows", [])}
    if before:
        L += _before_after(rows, before, (data.get("before") or {}).get("info", {}))
    before_budget = {r["part"]: r for r in (data.get("before_budget") or {}).get("rows", [])}
    if before_budget:
        L += _budget_table(rows, before_budget, (data.get("before_budget") or {}).get("info", {}))
    notes = data.get("notes") or []
    if notes:
        L += ["## Notes, failures and fixes", ""]
        L += [f"- {n}" for n in notes]
        L.append("")
    return "\n".join(L)


def _arrow(a, b, nd=4) -> str:
    if a is None and b is None:
        return "-"
    if a is None or b is None or _f(a, nd) == _f(b, nd):
        return _f(b if b is not None else a, nd)
    return f"{_f(a, nd)} → {_f(b, nd)}"


def _before_after(rows: list[dict], before: dict[str, dict], info_before: dict) -> list[str]:
    """The thin-feature refinement (task 013) part by part: what was found and what changed."""
    L = [
        "## Before and after the thin-feature refinement (task 013)",
        "",
        f"'Before' is the corpus run of {info_before.get('date', '?')} (task 012: the same parts, settings and "
        "machine, without the refinement band around thin parts); 'after' the run above. The 'thin parts' column "
        "is what the upload check found on the Quick mesh: the typical thickness of the plate-like parts thinner "
        "than 1.5 surface cells, the share of the surface they cover and how many cells span them; '-' means nothing "
        "is thin for that mesh and the mesh is unchanged (cells and times then only differ by the run-to-run noise of "
        "meshing). 'Refined to' is the band's level and cell size and the number of triangles in thin.stl, or "
        "'plain mesh' when the band's cell budget (task 014) left it out.",
        "",
        "| part | thin parts (Quick) | refined to | cells before → after | time before → after | CD before → after | "
        "CL before → after |",
        "|:--|:--|:--|---:|---:|---:|---:|",
    ]
    for r in rows:
        b = before.get(r["part"], {})
        L.append(f"| {r['part']} | {_thin_text(r)} | {_band_text(r)} | " + _change_cells(b, r))
    L.append("")
    return L


def _thin_text(r: dict) -> str:
    thin = r.get("thin") or {}
    return (f"{thin['thickness'] * 1000:.2g} mm, {thin['area_share'] * 100:.0f} % of the surface, "
            f"{thin['cells_across']:.1f} cells" if thin else "-")


def _band_text(r: dict) -> str:
    """The 'refined to' cell: the band's level, cell size and triangle count, or why there is none."""
    thin, ref = r.get("thin") or {}, r.get("thin_refinement") or {}
    if not thin:
        return "-"
    full, budget = _about(thin.get("full_added_cells")), f"{thin.get('cell_budget') or 0:,d}"
    if not ref:
        if thin.get("extra_levels") == 0:
            return f"plain mesh: the band would add about {full} cells, over the {budget} budget"
        return "-"
    out = f"level {ref.get('level')} ({thin['refined_cell'] * 1000:.2g} mm cells), {ref.get('triangles')} triangles"
    if thin.get("capped"):
        out += f"; one level less than wanted (the full band: about {full} cells, over the {budget} budget)"
    if thin.get("added_cells") is not None:
        out += f"; estimate +{_about(thin['added_cells'])} cells"
    return out


def _about(n) -> str:
    """A cell count to two significant figures: 425984 -> '430,000'."""
    n = int(n or 0)
    return f"{int(float(f'{n:.2g}')):,d}" if n >= 10 else str(n)


def _change_cells(b: dict, r: dict) -> str:
    mb, ma = (b.get("mesh") or {}).get("cells"), (r.get("mesh") or {}).get("cells")
    tb, ta = b.get("run_seconds"), r.get("run_seconds")
    rb, ra = b.get("result") or {}, r.get("result") or {}
    return (f"{_arrow(mb, ma, 6)} | {_arrow(tb, ta, 3)} s | {_arrow(rb.get('cd'), ra.get('cd'))} | "
            f"{_arrow(rb.get('cl'), ra.get('cl'), 3)} |")


def _budget_table(rows: list[dict], before: dict[str, dict], info_before: dict) -> list[str]:
    """The band's cell budget (task 014): the parts with thin features, before (task 013: the band at the level
    the thickness calls for, whatever the cost) and after (the budget applied)."""
    L = [
        "## The band's cell budget (task 014)",
        "",
        f"'Before' is the corpus run of {info_before.get('date', '?')} with the task-013 band (the level the "
        "thickness calls for, whatever it costs); 'after' is the run above with the cell budget: WindLab estimates "
        "the cells the band would add (`windlab.geometry.thin.band_cells`: the band itself, the extra boundary-layer "
        "cells and the ring of coarser cells it pushes outwards, all proportional to the thin area) and, above "
        "300,000, refines one level less, or leaves the mesh plain. Only the parts with thin plates or fins on the "
        "Quick mesh are listed; the others are untouched by the band and the budget alike.",
        "",
        "| part | thin parts (Quick) | band before | band after | cells before → after | time before → after | "
        "CD before → after | CL before → after |",
        "|:--|:--|:--|:--|---:|---:|---:|---:|",
    ]
    for r in rows:
        b = before.get(r["part"], {})
        if not (r.get("thin") or b.get("thin")):
            continue
        L.append(f"| {r['part']} | {_thin_text(r)} | {_band_text(b)} | {_band_text(r)} | " + _change_cells(b, r))
    L.append("")
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--np", type=int, default=4, dest="n_procs")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--data", type=Path, default=None, help="data folder for the app (default: a temp folder)")
    ap.add_argument("--notes", type=Path, default=OUT / "notes.json", help="JSON list of notes for the report")
    ap.add_argument("--before", type=Path, default=OUT / "results_task012.json",
                    help="an earlier results.json for the before/after table (task 012's by default)")
    ap.add_argument("--before-budget", type=Path, default=OUT / "results_task013.json",
                    help="the results.json with the band but without its cell budget (task 013's by default)")
    args = ap.parse_args()

    results = OUT / "results.json"
    if args.report_only:
        data = json.loads(results.read_text())
    else:
        from tests import stepgen as g

        names = args.only or list(CORPUS)
        parts = build_parts(g, names)
        write_parts(parts)
        if args.build_only:
            return 0
        import tempfile

        data_dir = args.data or Path(tempfile.mkdtemp(prefix="windlab-robust-"))
        rows = run_corpus(names, parts, args.n_procs, data_dir)
        old = json.loads(results.read_text())["rows"] if results.is_file() and args.only else []
        kept = [r for r in old if r["part"] not in names]
        data = {"info": {"date": time.strftime("%Y-%m-%d"), "machine": f"{machine.summary()['os']}, "
                         f"{socket.gethostname()}", "n_procs": args.n_procs, "airspeed": AIRSPEED, "alpha_deg": ALPHA,
                         "preset": PRESET, "version": __version__, "data_dir": str(data_dir)},
                "rows": sorted(kept + rows, key=lambda r: list(CORPUS).index(r["part"]))}
        results.write_text(json.dumps(data, indent=1) + "\n")
    if args.notes.is_file():
        data["notes"] = json.loads(args.notes.read_text())
    if args.before.is_file() and args.before.resolve() != results.resolve():
        data["before"] = json.loads(args.before.read_text())
    if args.before_budget.is_file() and args.before_budget.resolve() != results.resolve():
        data["before_budget"] = json.loads(args.before_budget.read_text())
    (OUT.parent / "robustness.md").write_text(render(data))
    print(f"\nwrote {results} and {OUT.parent / 'robustness.md'}")
    for r in data["rows"]:
        print(f"  {r['part']:14s} {r.get('status')}  cells {((r.get('mesh') or {}).get('cells'))}  "
              f"CD {((r.get('result') or {}).get('cd'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
