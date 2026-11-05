#!/usr/bin/env python3
"""2D NACA 0012 at Re = 6 million vs NASA TMR (k-omega SST, fully turbulent).

Runs alpha 0, 5, 10, 15 deg on the airfoil_2d template (structured C-grid, y+ < 1),
then writes validation/naca0012_tmr.json and validation/naca0012_tmr.md.
Case folders go in the runs folder (/runs by default), never in the repo.

    python scripts/validate_naca0012.py               # all four angles, 4 ranks
    python scripts/validate_naca0012.py --alpha 10 --np 8 --no-write
    python scripts/validate_naca0012.py --mesh-only --alpha 0 --no-write
    python scripts/validate_naca0012.py --rewrite     # redo md/json from the stored numbers
    python scripts/validate_naca0012.py --alpha 0 --merge   # rerun one angle, keep the other rows
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from windlab.airfoil2d import ZERO_ALPHA_CONVERGENCE, AirfoilMesh, build_airfoil_2d_case  # noqa: E402
from windlab.case_builder import DEFAULT_RUNS_ROOT  # noqa: E402
from windlab.runner import RunError, run_case  # noqa: E402

# NASA TMR SST results for the NACA 0012 at Re 6e6, M 0.15: CFL3D and FUN3D on the finest
# (897x257) family-I grid, read from the TMR SST page on 2026-11-03. (The TMR moved from
# turbmodels.larc.nasa.gov to tmbwg.github.io/turbmodels in 2025.) At alpha = 0 the TMR
# gives CL as "approximately 0" (symmetric section), so 0.0 is stored.
TMR_URL = "https://tmbwg.github.io/turbmodels/naca0012_val_sst.html"
TMR_GRID = "897x257"
TMR_CODES = ("cfl3d", "fun3d")
TMR_REFERENCE = {
    0.0: {"cfl3d": {"cl": 0.0, "cd": 0.00809}, "fun3d": {"cl": 0.0, "cd": 0.00808}},
    10.0: {"cfl3d": {"cl": 1.0778, "cd": 0.01236}, "fun3d": {"cl": 1.0840, "cd": 0.01253}},
    15.0: {"cfl3d": {"cl": 1.5068, "cd": 0.02219}, "fun3d": {"cl": 1.5109, "cd": 0.02275}},
}
# PRD section 9 acceptance for this case (solver chain): CL +/- 2 %, CD +/- 5 % at 10 deg,
# |CL| < 0.01 at 0 deg; 15 deg is reported as indicative only. The acceptance is judged
# against the MEAN of the two TMR codes, raw (no compressibility correction).
ACCEPT = {0.0: {"cl_abs": 0.01}, 10.0: {"cl_pct": 2.0, "cd_pct": 5.0}}
# TMR's runs are compressible at M = 0.15; simpleFoam is incompressible. To first order
# (Prandtl-Glauert) compressibility raises CL by 1/sqrt(1 - M^2) = 1.0114 at M = 0.15.
# The CL error against TMR mean / 1.0114 (its incompressible equivalent) is reported as
# an indicative column only; it is NOT used for the acceptance check.
TMR_MACH = 0.15
PG_FACTOR = 1.0 / (1.0 - TMR_MACH**2) ** 0.5


def reference_for(alpha: float) -> dict | None:
    """Both TMR codes plus their mean, or None when the TMR has no value at this angle."""
    ref = TMR_REFERENCE.get(alpha)
    if ref is None:
        return None
    out = {code: dict(ref[code]) for code in TMR_CODES}
    out["mean"] = {k: sum(ref[c][k] for c in TMR_CODES) / len(TMR_CODES) for k in ("cl", "cd")}
    return out


def errors_for(alpha: float, cl: float, cd: float) -> dict:
    """Error columns of one row: against each TMR code, against their mean (the acceptance
    value) and, for CL, against the Prandtl-Glauert-corrected mean (indicative)."""
    ref = reference_for(alpha)
    out = {"reference": ref}
    for key, value in (("cl", cl), ("cd", cd)):
        for code in (*TMR_CODES, "mean"):
            name = f"{key}_error_pct" if code == "mean" else f"{key}_error_{code}_pct"
            out[name] = pct(value, ref[code][key]) if ref else None
    out["cl_error_pg_pct"] = pct(cl, ref["mean"]["cl"] / PG_FACTOR) if ref else None
    out["check"] = verdict(alpha, cl, cd)
    return out


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
    elif kind == "startup_done":
        print(f"[{stamp}]   start-up done, final schemes from iteration {data['iteration']}", flush=True)
    elif kind == "converged":
        print(f"[{stamp}]   CL/CD settled at iteration {data['iteration']}", flush=True)


def pct(value: float, ref: float) -> float | None:
    return None if ref == 0 else 100.0 * (value - ref) / ref


def verdict(alpha: float, cl: float, cd: float) -> str:
    """PRD section 9 check against the mean of the two TMR codes, raw values."""
    acc = ACCEPT.get(alpha)
    ref = reference_for(alpha)
    if acc is None or ref is None:
        return "indicative" if ref else "no reference"
    mean = ref["mean"]
    ok = True
    if "cl_abs" in acc:
        ok &= abs(cl - mean["cl"]) < acc["cl_abs"]
    if "cl_pct" in acc:
        ok &= abs(pct(cl, mean["cl"])) <= acc["cl_pct"]
    if "cd_pct" in acc:
        ok &= abs(pct(cd, mean["cd"])) <= acc["cd_pct"]
    return "pass" if ok else "FAIL"


def fmt_pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1f} %"


def write_markdown(path: Path, rows: list[dict], info: dict) -> None:
    lines = [
        "# NACA 0012, Re = 6 million: WindLab vs NASA TMR",
        "",
        f"Generated by `scripts/validate_naca0012.py` on {info['date']} ({info['machine']}, "
        f"{info['n_procs']} MPI ranks). Raw numbers: `naca0012_tmr.json`.",
        "",
        "Setup: 2D (one cell thick, empty front/back, no tips), sharp-trailing-edge TMR NACA 0012, "
        f"chord 1 m, U = {info['speed']:g} m/s, nu = {info['nu']:.6g} m^2/s (Re = {info['reynolds']:.3g}, "
        f"Mach {info['mach']:.3f}, incompressible solver), simpleFoam k-omega SST, fully turbulent, "
        f"freestream Tu = {info['tu_percent']:g} %, nu_t/nu = {info['nut_ratio']:g}. "
        f"Structured C-grid from blockMesh, {info['cells']:,d} cells, far field {info['farfield']:g} chords, "
        f"first cell {info['first_cell']:g} c: **wall-resolved (y+ < 1), no wall functions**.",
        "",
        f"Reference values: NASA TMR SST results of CFL3D and FUN3D on the finest ({TMR_GRID}) grid, "
        f"<{TMR_URL}> (read 2026-11-03). At 0 deg the TMR gives CL as approximately 0. "
        "Acceptance from PRD section 9: CL within 2 % and CD within 5 % at 10 deg, |CL| < 0.01 at 0 deg, "
        "15 deg indicative only. **The check column judges the raw WindLab value against the mean of the "
        "two TMR codes** (columns *vs mean*).",
        "",
        f"TMR ran compressible at M = {TMR_MACH:g}, WindLab is incompressible. To first order "
        f"(Prandtl-Glauert) compressibility raises CL by {PG_FACTOR:.4f} at this Mach number; the column "
        "*vs mean (PG)* compares with the TMR mean divided by that factor, i.e. with its incompressible "
        "equivalent. It is indicative only and plays no part in the check.",
        "",
        "## Results",
        "",
        "| alpha | CL | CD | Cm (c/4) | y+ max (mean) | iterations | runtime | check |",
        "|---:|---:|---:|---:|---:|---:|---:|:---|",
    ]
    for r in rows:
        yp = r["y_plus"]
        sym = " (sym.)" if r.get("half_model") else ""
        lines.append(
            f"| {r['alpha']:g} | {r['cl']:.4f}{sym} | {r['cd']:.5f} | {round(r.get('cm') or 0, 4) + 0.0:.4f}{sym} | "
            f"{yp.get('max', 0):.2f} ({yp.get('average', 0):.2f}) | {r['iterations']}"
            f"{'' if r['converged'] else ' (cap)'} | {r['runtime_s']:.0f} s | {r['check']} |"
        )
    if any(r.get("half_model") for r in rows):
        lines += ["", "(sym.): alpha = 0 is run as the upper half of the section with a symmetry plane on the chord "
                  "line and the wake cut, so CL and Cm are zero by construction and only CD is a result (the "
                  "full-model run of 2026-11-03 gave CL = -0.0011 at 0 deg; the full model's wake oscillates "
                  "along the C-grid cut and diverged in two of four attempts)."]
    lines += [
        "",
        "## Lift coefficient against the TMR",
        "",
        "| alpha | CL WindLab | CL CFL3D | CL FUN3D | CL mean | vs CFL3D | vs FUN3D | vs mean | vs mean (PG) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        ref = r["reference"]
        if not ref:
            lines.append(f"| {r['alpha']:g} | {r['cl']:.4f} | - | - | - | - | - | no reference | - |")
            continue
        if ref["mean"]["cl"] == 0:  # absolute differences: a relative error to zero is meaningless
            errs = [f"{r['cl'] - ref[c]['cl']:+.4f} abs" for c in (*TMR_CODES, "mean")] + ["-"]
        else:
            errs = [fmt_pct(r[k]) for k in ("cl_error_cfl3d_pct", "cl_error_fun3d_pct", "cl_error_pct",
                                             "cl_error_pg_pct")]
        lines.append(
            f"| {r['alpha']:g} | {r['cl']:.4f} | {ref['cfl3d']['cl']:.4f} | {ref['fun3d']['cl']:.4f} | "
            f"{ref['mean']['cl']:.4f} | " + " | ".join(errs) + " |"
        )
    lines += [
        "",
        "## Drag coefficient against the TMR",
        "",
        "| alpha | CD WindLab | CD CFL3D | CD FUN3D | CD mean | vs CFL3D | vs FUN3D | vs mean |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        ref = r["reference"]
        if not ref:
            lines.append(f"| {r['alpha']:g} | {r['cd']:.5f} | - | - | - | - | - | no reference |")
            continue
        lines.append(
            f"| {r['alpha']:g} | {r['cd']:.5f} | {ref['cfl3d']['cd']:.5f} | {ref['fun3d']['cd']:.5f} | "
            f"{ref['mean']['cd']:.5f} | {fmt_pct(r['cd_error_cfl3d_pct'])} | {fmt_pct(r['cd_error_fun3d_pct'])} | "
            f"{fmt_pct(r['cd_error_pct'])} |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "Runtime is the wall clock for the whole case (blockMesh to the last solver iteration).",
        f"checkMesh: max non-orthogonality {rows[0]['max_non_orthogonality']:.0f} deg; the one failed "
        "check is 'high aspect ratio', expected for y+ < 1 cells.",
        *(["Iterations marked (cap) hit the iteration limit before the settling rule was met."]
          if any(not r["converged"] for r in rows) else []),
        *info.get("footnotes", []),
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha", type=float, nargs="+", default=[0.0, 5.0, 10.0, 15.0])
    ap.add_argument("--np", type=int, default=4, dest="n_procs")
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS_ROOT)
    ap.add_argument("--out", type=Path, default=REPO / "validation")
    ap.add_argument("--mesh-only", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="do not write the json/md results")
    ap.add_argument("--merge", action="store_true",
                    help="replace only the rows for --alpha in the stored json/md, keep the other angles")
    ap.add_argument("--rewrite", action="store_true",
                    help="recompute errors/checks from the stored json and rewrite json + md (no solver runs)")
    args = ap.parse_args()
    if args.rewrite:
        return rewrite(args.out)

    mesh = AirfoilMesh()
    rows, case_info = [], None
    for alpha in args.alpha:
        case = build_airfoil_2d_case(alpha, n_procs=args.n_procs, runs_root=args.runs, mesh=mesh)
        p = case.params
        case_info = p
        print(f"\n=== NACA 0012 2D, alpha {alpha:g} deg, Re {p['reynolds']:.3g}, {p['n_procs']} ranks, "
              f"{p['cells_planned']:,d} cells {p['cell_counts']}")
        print(f"    case: {case.path}", flush=True)
        try:
            rep = run_case(case.path, progress=progress, mesh_only=args.mesh_only)
        except RunError as e:
            print(f"RUN FAILED in {e.stage}: {e}\n    log: {e.log}")
            return 1
        if args.mesh_only:
            print(f"    mesh: {rep.mesh}")
            continue
        c = rep.coefficients
        half = bool(p.get("half_model"))
        row = {
            "alpha": alpha,
            # half model (symmetry plane): CL and Cm are zero by construction, CD is the
            # full section's (Aref is halved)
            "cl": 0.0 if half else c["cl"],
            "cl_pm": 0.0 if half else c["cl_pm"],
            "cd": c["cd"],
            "cd_pm": c["cd_pm"],
            "cm": 0.0 if half else c.get("cm"),
            "half_model": half,
            **errors_for(alpha, 0.0 if half else c["cl"], c["cd"]),
            "y_plus": rep.y_plus.get("airfoil", {}),
            "iterations": rep.iterations,
            "converged": rep.converged,
            "runtime_s": rep.total_seconds,
            "stages": {s.name: round(s.seconds, 1) for s in rep.stages},
            "peak_rss_mb": rep.peak_rss_mb,
            "cells": rep.mesh.get("cells"),
            "max_non_orthogonality": rep.mesh.get("max_non_orthogonality"),
            "failed_mesh_checks": rep.mesh.get("failed_checks"),
            "run_id": rep.run_id,
            "notes": rep.notes,
        }
        rows.append(row)
        print(f"    CL {row['cl']:.4f}  CD {row['cd']:.5f}  ({row['check']}), {row['runtime_s']:.0f} s")

    if args.mesh_only or not rows:
        return 0

    info = {
        "date": time.strftime("%Y-%m-%d"),
        "machine": f"{platform.machine()} {platform.system()} container",
        "n_procs": case_info["n_procs"],
        "speed": case_info["airspeed"],
        "nu": case_info["nu"],
        "reynolds": case_info["reynolds"],
        "mach": case_info["mach"],
        "tu_percent": 100 * (case_info["k_inf"] / 1.5) ** 0.5 / case_info["airspeed"],
        "nut_ratio": case_info["nut_inf"] / case_info["nu"],
        "cells": rows[0]["cells"] or case_info["cells_planned"],
        "farfield": mesh.farfield,
        "first_cell": mesh.first_cell,
        "mesh_controls": case_info["mesh_controls"],
        "convergence": case_info["convergence"],
        "label": "validation of the solver chain only - not of the snappyHexMesh wing presets",
        **reference_info(),
    }
    print("\n" + json.dumps([{k: r[k] for k in ("alpha", "cl", "cd", "check", "runtime_s")} for r in rows], indent=1))
    if args.merge:
        rows, info = merge_rows(args.out / "naca0012_tmr.json", rows, info)
    if not args.no_write:
        args.out.mkdir(exist_ok=True)
        (args.out / "naca0012_tmr.json").write_text(json.dumps({"info": info, "results": rows}, indent=2) + "\n")
        write_markdown(args.out / "naca0012_tmr.md", rows, info)
        print(f"wrote {args.out / 'naca0012_tmr.json'} and naca0012_tmr.md")
    return 0


def merge_rows(path: Path, new_rows: list[dict], info: dict) -> tuple[list[dict], dict]:
    """Replace only the rerun angles in the stored results; keep every other row and its date."""
    if not path.is_file():
        return new_rows, info
    old = json.loads(path.read_text())
    rerun = {r["alpha"] for r in new_rows}
    for r in new_rows:
        r["date"] = info["date"]
    kept = [r for r in old["results"] if r["alpha"] not in rerun]
    for r in kept:
        r.setdefault("date", old["info"]["date"])
    rows = sorted(kept + new_rows, key=lambda r: r["alpha"])
    merged = {**old["info"], "n_procs": info["n_procs"], "machine": info["machine"], "updated": info["date"]}
    angles = ", ".join(f"{a:g}" for a in sorted(rerun))
    notes = [
        f"Rows for alpha = {angles} deg were rerun on {info['date']} (`--alpha {angles} --merge`); "
        f"the other rows are from the solver runs of {old['info']['date']}.",
    ]
    for r in new_rows:
        prev = next((o for o in old["results"] if o["alpha"] == r["alpha"]), None)
        if prev:
            notes.append(
                f"Previous {r['alpha']:g} deg result ({prev.get('date', old['info']['date'])}): CL {prev['cl']:.4f}, "
                f"CD {prev['cd']:.5f}, {prev['iterations']} iterations{'' if prev['converged'] else ' (cap)'}, "
                f"{prev['runtime_s']:.0f} s; the new CD differs from it by {pct(r['cd'], prev['cd']):+.1f} %."
            )
    if 0.0 in rerun:
        z = ZERO_ALPHA_CONVERGENCE
        notes.append("The 0 deg row uses the zero-incidence stop rule (`windlab.airfoil2d.ZERO_ALPHA_CONVERGENCE`: "
                     f"at least {z['min_iterations']} iterations, then CD settled to {100 * z['cd_tol']:g} % between "
                     f"{z['window']}-iteration means {z['lag']} iterations apart); the other angles use the strict "
                     "validation rule.")
    merged["footnotes"] = notes
    return rows, merged


def reference_info() -> dict:
    return {
        "tmr_url": TMR_URL,
        "tmr_grid": TMR_GRID,
        "tmr_codes": list(TMR_CODES),
        "tmr_reference": {f"{a:g}": reference_for(a) for a in sorted(TMR_REFERENCE)},
        "tmr_mach": TMR_MACH,
        "prandtl_glauert_factor": PG_FACTOR,
        "acceptance": "PRD section 9, judged against the mean of CFL3D and FUN3D, raw (no Prandtl-Glauert "
                      "correction); cl_error_pg_pct is indicative only",
    }


def rewrite(out: Path) -> int:
    """Recompute the derived columns from the stored CL/CD and rewrite json + md."""
    path = out / "naca0012_tmr.json"
    data = json.loads(path.read_text())
    info, rows = data["info"], data["results"]
    for r in rows:
        r.update(errors_for(r["alpha"], r["cl"], r["cd"]))
    info.pop("cl_acceptance", None)
    info.update(reference_info())
    info["rewritten"] = time.strftime("%Y-%m-%d")
    note = (f"Tables regenerated on {info['rewritten']} from the stored solver numbers (`--rewrite`, no new "
            "solver runs).")
    info["footnotes"] = [f for f in info.get("footnotes", []) if not f.startswith("Tables regenerated")] + [note]
    path.write_text(json.dumps({"info": info, "results": rows}, indent=2) + "\n")
    write_markdown(out / "naca0012_tmr.md", rows, info)
    for r in rows:
        print(f"alpha {r['alpha']:4g}: CL vs mean {fmt_pct(r['cl_error_pct'])} (PG {fmt_pct(r['cl_error_pg_pct'])}), "
              f"CD vs mean {fmt_pct(r['cd_error_pct'])} -> {r['check']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
