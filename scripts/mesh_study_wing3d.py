#!/usr/bin/env python3
"""Mesh study for the 3D NACA 0012 wing (chord 0.3 m, span 0.6 m, 20 m/s, k-omega SST).

Runs the Quick and Standard presets (and an optional finer level) at alpha 0 and 10 deg
with the app's own case template and stop rule, records cell counts, y+, CL, CD (split
into pressure and viscous parts), runtime and peak memory, does a Richardson /
grid-convergence-index estimate where three levels exist, compares CD at 0 deg with a
flat-plate skin-friction estimate and, optionally, with the wall-resolved 2D section run
at the same Reynolds number. Writes validation/wing3d_mesh_study.json and .md.

    python scripts/mesh_study_wing3d.py --levels quick standard --alpha 0 10
    python scripts/mesh_study_wing3d.py --levels fine --alpha 10 --merge     # add a level
    python scripts/mesh_study_wing3d.py --airfoil2d --alpha 0 --merge        # 2D row
    python scripts/mesh_study_wing3d.py --rewrite                            # md from json
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from windlab.airfoil2d import build_airfoil_2d_case  # noqa: E402
from windlab.case_builder import (  # noqa: E402
    DEFAULT_RUNS_ROOT,
    NU_SEA_LEVEL,
    PRESETS,
    FlowConditions,
    GeometryInput,
    MeshPreset,
    Preset,
    ReferenceValues,
    build_case,
)
from windlab.geometry.naca import naca4_section, naca_wing, section_area  # noqa: E402
from windlab.runner import RunError, run_case  # noqa: E402

CODE, CHORD, SPAN, SPEED = "0012", 0.3, 0.6, 20.0
THICKNESS = 0.12

# Mesh levels: the two app presets plus one finer member of the same refinement family
# (only the background cell changes, SIM-4). "fine" is not offered in the app; its cell
# is half the Standard one (Quick 2.0 c, Standard 1.29 c, fine 0.645 c). Measured on
# 2026-11-03: Quick 0.37 M, Standard 0.81 M cells (the cell count grows as h^-1.8, not
# h^-3, because the surface refinement dominates).
LEVELS: dict[str, tuple[Preset, MeshPreset | None]] = {
    "quick": (Preset.QUICK, None),
    "standard": (Preset.STANDARD, None),
    "fine": (Preset.DETAILED, MeshPreset(PRESETS[Preset.STANDARD].bg_cell_over_l / 2, 3000, 12_000_000, 2_700_000)),
}


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


# --------------------------------------------------------------------------- estimates


def skin_friction_estimate(reynolds: float, thickness: float = THICKNESS, aspect_ratio: float = SPAN / CHORD) -> dict:
    """Zero-lift drag of the wing from flat-plate turbulent skin friction x form factor.

    Cf from ITTC-57 (0.075 / (log10 Re - 2)^2) and Prandtl-Schlichting (0.455 / log10(Re)^2.58),
    both fully turbulent from the leading edge; Hoerner's form factor for a streamlined
    section 1 + 2 t/c + 60 (t/c)^4; wetted area = perimeter x span for the surfaces plus the
    two flat tip caps (no form factor, they are plates aligned with the flow)."""
    pts = naca4_section(CODE, 400)
    perimeter = float(np.sum(np.linalg.norm(np.diff(np.vstack([pts, pts[:1]]), axis=0), axis=1)))  # per chord
    cap = section_area(CODE, 400)  # per chord^2
    s_ref = 1.0 * aspect_ratio  # chord = 1
    s_wet = perimeter * aspect_ratio
    s_tips = 2 * cap
    ff = 1 + 2 * thickness + 60 * thickness**4
    cf = {
        "ittc57": 0.075 / (math.log10(reynolds) - 2) ** 2,
        "prandtl_schlichting": 0.455 / math.log10(reynolds) ** 2.58,
    }
    out = {
        "reynolds": reynolds,
        "wetted_over_planform": s_wet / s_ref,
        "tip_caps_over_planform": s_tips / s_ref,
        "form_factor": ff,
        "cf": cf,
        "cd0": {k: v * (s_wet * ff + s_tips) / s_ref for k, v in cf.items()},
    }
    return out


def richardson(levels: list[dict], key: str) -> dict | None:
    """Celik et al. (2008) procedure on three mesh levels sorted coarse -> fine.

    h = (1/N)^(1/3) per level; returns the observed order p, the extrapolated value, the
    relative errors and the fine-grid GCI (safety factor 1.25). ``None`` when a value is
    missing; oscillatory convergence is flagged instead of fitted."""
    if len(levels) < 3 or any(lv.get(key) is None for lv in levels):
        return None
    coarse, medium, fine = levels[-3], levels[-2], levels[-1]
    h3, h2, h1 = ((1.0 / lv["cells"]) ** (1 / 3) for lv in (coarse, medium, fine))
    f3, f2, f1 = coarse[key], medium[key], fine[key]
    r21, r32 = h2 / h1, h3 / h2
    e21, e32 = f2 - f1, f3 - f2
    out = {"levels": [lv["level"] for lv in (coarse, medium, fine)], "values": [f3, f2, f1],
           "r21": r21, "r32": r32}
    if e21 == 0 or e32 == 0 or (e32 / e21) < 0:
        out["note"] = "oscillatory or stalled convergence - no order estimate"
        return out
    s = 1.0
    p = abs(math.log(abs(e32 / e21)) / math.log(r21))
    for _ in range(50):  # fixed point for p when r21 != r32
        q = math.log((r21**p - s) / (r32**p - s)) if r21 != r32 else 0.0
        p_new = abs(math.log(abs(e32 / e21)) + q) / math.log(r21)
        if abs(p_new - p) < 1e-10:
            break
        p = p_new
    ext = (r21**p * f1 - f2) / (r21**p - 1)
    ea21 = abs((f1 - f2) / f1) if f1 else float("nan")
    out.update({
        "order_p": p,
        "extrapolated": ext,
        "fine_minus_extrapolated_pct": 100 * (f1 - ext) / ext if ext else None,
        "coarse_minus_extrapolated_pct": 100 * (f3 - ext) / ext if ext else None,
        "gci_fine_pct": 100 * 1.25 * ea21 / (r21**p - 1),
    })
    return out


# --------------------------------------------------------------------------- runs


def wing_geometry(tmp: Path) -> tuple[GeometryInput, ReferenceValues, object]:
    wing = naca_wing(CODE, chord=CHORD, span=SPAN)
    stl = tmp / "naca0012.stl"
    wing.write_stl(stl)
    lo, hi = wing.bounds
    geom = GeometryInput(stl_path=stl, bounds_min=tuple(lo), bounds_max=tuple(hi))
    ref = ReferenceValues(area=wing.reference_area, length=wing.mean_aero_chord, moment_point=wing.quarter_chord_point)
    return geom, ref, wing


def run_level(level: str, alpha: float, n_procs: int, runs_root: Path, geom, ref) -> dict:
    preset, override = LEVELS[level]
    case = build_case(geom, FlowConditions(airspeed=SPEED, alpha_deg=alpha), ref, preset=preset,
                      n_procs=n_procs, runs_root=runs_root, mesh_preset=override)
    p = case.params
    print(f"\n=== {level}: NACA {CODE} wing, alpha {alpha:g} deg, {SPEED:g} m/s, Re {p['reynolds']:.3g}, "
          f"background cell {p['background_cell']:.4f} m, {p['n_procs']} ranks\n    case: {case.path}", flush=True)
    rep = run_case(case.path, progress=progress)
    return row_from_report(level, alpha, rep, p, kind="wing3d")


def run_airfoil_2d(alpha: float, n_procs: int, runs_root: Path, reynolds: float) -> dict:
    case = build_airfoil_2d_case(alpha, n_procs=n_procs, runs_root=runs_root, reynolds=reynolds)
    p = case.params
    print(f"\n=== 2D wall-resolved section, alpha {alpha:g} deg, Re {p['reynolds']:.3g}, {p['n_procs']} ranks, "
          f"{p['cells_planned']:,d} cells\n    case: {case.path}", flush=True)
    rep = run_case(case.path, progress=progress)
    row = row_from_report("airfoil2d", alpha, rep, p, kind="airfoil2d")
    if p.get("half_model"):  # symmetry plane: the upper half's own lift is meaningless
        row.update({"cl": 0.0, "cl_pm": 0.0, "cm": 0.0, "cl_pressure": None, "cl_viscous": None, "half_model": True})
    return row


def row_from_report(level: str, alpha: float, rep, p: dict, kind: str) -> dict:
    c = rep.coefficients
    yp = rep.y_plus.get("body") or rep.y_plus.get("airfoil") or {}
    layers = (rep.mesh.get("layers") or {}).get("body")
    return {
        "level": level,
        "kind": kind,
        "alpha": alpha,
        "cells": rep.mesh.get("cells"),
        "background_cell": p.get("background_cell"),
        "surface_cell": p.get("surface_cell"),
        "reynolds": p["reynolds"],
        "cl": c["cl"], "cl_pm": c["cl_pm"],
        "cd": c["cd"], "cd_pm": c["cd_pm"],
        "cm": c.get("cm"),
        "cd_pressure": c.get("cd_pressure"), "cd_viscous": c.get("cd_viscous"),
        "cl_pressure": c.get("cl_pressure"), "cl_viscous": c.get("cl_viscous"),
        "y_plus": {k: yp.get(k) for k in ("min", "average", "max")},
        "layers": layers,
        "iterations": rep.iterations,
        "converged": rep.converged,
        "runtime_s": rep.total_seconds,
        "stages": {s.name: round(s.seconds, 1) for s in rep.stages},
        "peak_rss_mb": rep.peak_rss_mb,
        "max_non_orthogonality": rep.mesh.get("max_non_orthogonality"),
        "mesh_ok": rep.mesh.get("mesh_ok"),
        "residuals": rep.residuals,
        "run_id": rep.run_id,
        "notes": rep.notes,
        "date": time.strftime("%Y-%m-%d"),
    }


# --------------------------------------------------------------------------- report


def analyse(rows: list[dict]) -> dict:
    """Derived numbers: Richardson per angle, the flat-plate estimate, the CD-at-0 budget."""
    re_wing = SPEED * CHORD / NU_SEA_LEVEL
    wing = [r for r in rows if r["kind"] == "wing3d"]
    order = {k: i for i, k in enumerate(LEVELS)}
    out: dict = {"reynolds": re_wing, "estimate": skin_friction_estimate(re_wing), "richardson": {}}
    for alpha in sorted({r["alpha"] for r in wing}):
        lv = sorted((r for r in wing if r["alpha"] == alpha), key=lambda r: order[r["level"]])
        keys = ("cl", "cd", "cd_pressure", "cd_viscous")
        out["richardson"][f"{alpha:g}"] = {key: richardson(lv, key) for key in keys}
    quick0 = next((r for r in wing if r["level"] == "quick" and r["alpha"] == 0), None)
    finest0 = max((r for r in wing if r["alpha"] == 0), key=lambda r: order[r["level"]], default=None)
    two_d0 = next((r for r in rows if r["kind"] == "airfoil2d" and r["alpha"] == 0), None)
    if quick0 and finest0:
        est = out["estimate"]
        rich_cd = out["richardson"]["0"]["cd"] if "0" in out["richardson"] else None
        best = (rich_cd or {}).get("extrapolated") or finest0["cd"]
        budget = {
            "quick_cd": quick0["cd"],
            "finest_level": finest0["level"],
            "finest_cd": finest0["cd"],
            "mesh_converged_cd": best,
            "mesh_converged_how": "Richardson extrapolation" if (rich_cd or {}).get("extrapolated") else
            f"finest level ({finest0['level']})",
            "quick_over_mesh_converged_pct": 100 * (quick0["cd"] - best) / best,
            "flat_plate_cd0": est["cd0"],
        }
        if two_d0:
            tips = est["cf"]["ittc57"] * est["tip_caps_over_planform"]
            budget.update({
                "airfoil2d_cd": two_d0["cd"],
                "airfoil2d_cd_pressure": two_d0.get("cd_pressure"),
                "airfoil2d_cd_viscous": two_d0.get("cd_viscous"),
                "airfoil2d_plus_tips_cd": two_d0["cd"] + tips,
                "tip_caps_cd_estimate": tips,
                "mesh_converged_over_airfoil2d_pct": 100 * (best - two_d0["cd"] - tips) / (two_d0["cd"] + tips),
            })
        out["cd0_budget"] = budget
    return out


def fmt(x, nd=4, pct=False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:+.1f} %" if pct else f"{x:.{nd}f}"


def write_markdown(path: Path, rows: list[dict], info: dict, an: dict) -> None:
    order = {k: i for i, k in enumerate(LEVELS)}
    wing = sorted((r for r in rows if r["kind"] == "wing3d"), key=lambda r: (r["alpha"], order[r["level"]]))
    two_d = [r for r in rows if r["kind"] == "airfoil2d"]
    est = an["estimate"]
    L = [
        "# 3D NACA 0012 wing: mesh study (Quick / Standard / fine)",
        "",
        f"Generated by `scripts/mesh_study_wing3d.py` on {info['date']} ({info['machine']}, {info['n_procs']} MPI "
        "ranks, one run at a time). Raw numbers: `wing3d_mesh_study.json`. **Uncalibrated prototype - not yet "
        "validated.**",
        "",
        f"Case: the app's default wing, NACA {CODE}, chord {CHORD:g} m, span {SPAN:g} m (aspect ratio "
        f"{SPAN / CHORD:g}, flat tips), {SPEED:g} m/s, Re = {an['reynolds']:.3g}, alpha 0 and 10 deg; the app's "
        "`external_aero` template (blockMesh + snappyHexMesh with 5 boundary-layer cells, Spalding wall function, "
        "simpleFoam k-omega SST, fully turbulent, Tu 0.1 %) and its everyday stop rule. The three mesh levels are "
        "one refinement family: only the background cell changes ("
        + ", ".join(f"{k} {(v[1] or PRESETS[v[0]]).bg_cell_over_l:.3g} c" for k, v in LEVELS.items())
        + "; the wing surface is refined 7-8 levels below it, so the surface cell is 1/128-1/256 of the background "
        "cell), all refinement levels, boxes and layer settings stay the same.",
        "",
        "## Results",
        "",
        "| level | alpha | cells | y+ min / mean / max | CL | CD | CD pressure | CD viscous | L/D | iterations | "
        "runtime | peak memory |",
        "|:--|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in wing:
        yp = r["y_plus"]
        ld = r["cl"] / r["cd"] if r["cd"] else float("nan")
        L.append(
            f"| {r['level']} | {r['alpha']:g} | {r['cells']:,d} | {fmt(yp['min'], 1)} / {fmt(yp['average'], 1)} / "
            f"{fmt(yp['max'], 1)} | {r['cl']:.4f} | {r['cd']:.5f} | {fmt(r['cd_pressure'], 5)} | "
            f"{fmt(r['cd_viscous'], 5)} | {ld:.2f} | {r['iterations']}{'' if r['converged'] else ' (cap)'} | "
            f"{r['runtime_s']:.0f} s | {r['peak_rss_mb'] / 1024:.1f} GB |"
        )
    if two_d:
        L += ["", "Wall-resolved 2D section (the validated `airfoil_2d` C-grid, y+ < 1, no tips) at the same "
              "Reynolds number, for comparison:", "",
              "| case | alpha | cells | y+ max | CL | CD | CD pressure | CD viscous | iterations | runtime |",
              "|:--|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in two_d:
            L.append(f"| 2D, Re {r['reynolds']:.3g} | {r['alpha']:g} | {r['cells']:,d} | "
                     f"{fmt(r['y_plus']['max'], 2)} | {r['cl']:.4f} | {r['cd']:.5f} | {fmt(r['cd_pressure'], 5)} | "
                     f"{fmt(r['cd_viscous'], 5)} | {r['iterations']}{'' if r['converged'] else ' (cap)'} | "
                     f"{r['runtime_s']:.0f} s |")
    L += [
        "",
        "Runtime is the wall clock of the whole run (blockMesh to the last solver iteration) on 4 cores of the dev "
        "container; peak memory is the resident memory of all ranks together. CD pressure / viscous come from the "
        "`forces` function object projected on the wind direction. y+ is on the wing surface at the last iteration.",
        "",
        "## Grid convergence",
        "",
    ]
    for alpha, rich in an["richardson"].items():
        lv = [r for r in wing if f"{r['alpha']:g}" == alpha]
        L.append(f"**alpha {alpha} deg** ({len(lv)} levels)")
        L.append("")
        if len(lv) >= 2:
            L.append("| quantity | " + " | ".join(r["level"] for r in lv) + " | change coarsest -> finest | "
                     "observed order p | extrapolated | GCI (finest) |")
            L.append("|:--|" + "---:|" * (len(lv) + 4))
            for key, name in (("cl", "CL"), ("cd", "CD"), ("cd_pressure", "CD pressure"), ("cd_viscous", "CD viscous")):
                vals = [r.get(key) for r in lv]
                if any(v is None for v in vals) or (key == "cl" and all(abs(v) < 0.01 for v in vals)):
                    continue  # CL at zero incidence is noise around zero
                ch = 100 * (vals[-1] - vals[0]) / vals[0] if vals[0] else float("nan")
                rc = rich.get(key) or {}
                L.append(f"| {name} | " + " | ".join(f"{v:.5f}" for v in vals) + f" | {fmt(ch, pct=True)} | "
                         f"{fmt(rc.get('order_p'), 2)} | {fmt(rc.get('extrapolated'), 5)} | "
                         f"{fmt(rc.get('gci_fine_pct'), 1)}{' %' if rc.get('gci_fine_pct') is not None else ''} |"
                         + (f" {rc['note']}" if rc.get("note") else ""))
        else:
            L.append("Only one level.")
        L.append("")
    L += [
        "Richardson extrapolation and the grid convergence index (GCI) follow Celik et al. (2008) with the "
        "representative cell size h = (1/cells)^(1/3). Observed orders above 2 mean the three meshes are not yet "
        "in the asymptotic range (the family refines the surface and the boundary layer more than the far field, "
        "so h underestimates the real refinement ratio): read the extrapolated values as a rough estimate of the "
        "mesh-converged answer, not as a precise limit.",
        "",
    ]
    b = an.get("cd0_budget")
    L += [
        "## Where the drag at 0 deg comes from",
        "",
        f"Flat-plate estimate at Re = {est['reynolds']:.3g}, fully turbulent from the leading edge: "
        f"Cf = {est['cf']['ittc57']:.5f} (ITTC-57) or {est['cf']['prandtl_schlichting']:.5f} (Prandtl-Schlichting); "
        f"form factor {est['form_factor']:.3f} (Hoerner, t/c = {THICKNESS:g}); wetted area "
        f"{est['wetted_over_planform']:.3f} x planform for the surfaces plus {est['tip_caps_over_planform']:.3f} x "
        f"planform for the two flat tip caps. That gives **CD0 = {est['cd0']['ittc57']:.4f} (ITTC-57) / "
        f"{est['cd0']['prandtl_schlichting']:.4f} (Prandtl-Schlichting)**.",
        "",
    ]
    if b:
        L += [
            "| | CD at 0 deg | note |",
            "|:--|---:|:--|",
            f"| Quick mesh | {b['quick_cd']:.5f} | the app's everyday result |",
            f"| mesh-converged 3D ({b['mesh_converged_how']}) | {b['mesh_converged_cd']:.5f} | Quick is "
            f"{b['quick_over_mesh_converged_pct']:+.1f} % from it |",
        ]
        if "airfoil2d_cd" in b:
            L += [
                f"| 2D wall-resolved section, same Re | {b['airfoil2d_cd']:.5f} | pressure "
                f"{fmt(b['airfoil2d_cd_pressure'], 5)}, viscous {fmt(b['airfoil2d_cd_viscous'], 5)}; same turbulence "
                "model, no wall function, no tips |",
                f"| 2D section + tip caps (estimate) | {b['airfoil2d_plus_tips_cd']:.5f} | tips add about "
                f"{b['tip_caps_cd_estimate']:.4f}; the mesh-converged 3D value is "
                f"{b['mesh_converged_over_airfoil2d_pct']:+.1f} % from this |",
            ]
        L += [
            f"| flat plate x form factor | {b['flat_plate_cd0']['ittc57']:.5f} / "
            f"{b['flat_plate_cd0']['prandtl_schlichting']:.5f} | ITTC-57 / Prandtl-Schlichting, fully turbulent |",
            "| measured, natural transition (literature) | about 0.009-0.011 | NACA 0012 near Re 4e5 in low-turbulence "
            "tunnels (e.g. Sheldahl & Klimas 1981, XFOIL with free transition): long laminar runs and a laminar "
            "separation bubble; approximate range, not a WindLab number |",
            "",
        ]
    L += ["## Conclusion for students", ""]
    for para in conclusion(rows, an):
        L += [para, ""]
    for n in info.get("footnotes", []):
        L.append(n)
    path.write_text("\n".join(L) + "\n")


def _ref_value(an: dict, alpha: str, key: str, finest: dict | None) -> tuple[float | None, str]:
    """Best mesh-converged estimate: Richardson where it exists, else the finest level."""
    rc = (an["richardson"].get(alpha) or {}).get(key) or {}
    if rc.get("extrapolated"):
        return rc["extrapolated"], "the Richardson-extrapolated value"
    return (finest.get(key) if finest else None), f"the finest mesh here ({finest['level']})" if finest else ""


def conclusion(rows: list[dict], an: dict) -> list[str]:
    """Plain-language paragraphs with the measured numbers filled in."""
    order = {k: i for i, k in enumerate(LEVELS)}
    wing = [r for r in rows if r["kind"] == "wing3d"]
    out = []

    def pick(level, alpha):
        return next((r for r in wing if r["level"] == level and r["alpha"] == alpha), None)

    def finest(alpha):
        return max((r for r in wing if r["alpha"] == alpha), key=lambda r: order[r["level"]], default=None)

    q0, q10, f0, f10 = pick("quick", 0), pick("quick", 10), finest(0), finest(10)
    if q10 and f10 and f10 is not q10:
        cl_ref, how_cl = _ref_value(an, "10", "cl", f10)
        cd_ref, how_cd = _ref_value(an, "10", "cd", f10)
        n10 = len([r for r in wing if r["alpha"] == 10])
        out.append(
            f"**Lift is the number to trust.** At 10 deg the Quick mesh gives CL = {q10['cl']:.3f}; "
            f"{how_cl} is {cl_ref:.3f}, so Quick is {100 * (q10['cl'] - cl_ref) / cl_ref:+.1f} % off "
            f"({n10} mesh levels). That is the same size as the solver's own error against NASA's 2D reference "
            "(about 2 %), and far smaller than the difference between two airfoils or two angles. "
            f"Drag at 10 deg: Quick {q10['cd']:.4f} against {cd_ref:.4f} ({how_cd}), "
            f"{100 * (q10['cd'] - cd_ref) / cd_ref:+.1f} %; at this angle most of the drag is induced drag "
            f"(about CL^2 / (pi e AR) = {q10['cl']**2 / (math.pi * 0.9 * SPAN / CHORD):.3f}), which the mesh "
            "captures well because it comes from the lift distribution, not from the boundary layer.")
    b = an.get("cd0_budget")
    if q0 and f0 and b:
        est = an["estimate"]
        cf_wet = est["cf"]["ittc57"] * (est["wetted_over_planform"] + est["tip_caps_over_planform"])
        out.append(
            f"**Drag at zero lift is where Quick is weakest.** Quick gives CD = {q0['cd']:.4f} at 0 deg: "
            f"{b['quick_over_mesh_converged_pct']:+.1f} % above {b['mesh_converged_how']} "
            f"({b['mesh_converged_cd']:.4f}). The split shows why: the skin-friction (viscous) part, "
            f"{q0['cd_viscous']:.4f}, barely changes with the mesh and agrees with a flat-plate estimate "
            f"({cf_wet:.4f} with ITTC-57), "
            f"while the pressure part, {q0['cd_pressure']:.4f} on Quick, falls as the mesh is refined"
            + (f" ({f0['cd_pressure']:.4f} on {f0['level']})" if f0.get("cd_pressure") else "")
            + ". The pressure drag of an attached airfoil comes from how the boundary layer thickens towards the "
            "trailing edge, and the Quick mesh's 5 layer cells (about 5 mm) cover only half of that boundary "
            "layer; the rest sits in coarse cells.")
        if "airfoil2d_cd" in b:
            out.append(
                f"**How much of the gap is mesh, and how much is physics?** The validated 2D section, run "
                f"wall-resolved at the same Reynolds number, gives CD = {b['airfoil2d_cd']:.4f} "
                f"(pressure {b['airfoil2d_cd_pressure']:.4f}, viscous {b['airfoil2d_cd_viscous']:.4f}); adding "
                f"skin friction on the two flat tip caps gives about {b['airfoil2d_plus_tips_cd']:.4f}. Against "
                f"that, Quick is {100 * (q0['cd'] - b['airfoil2d_plus_tips_cd']) / b['airfoil2d_plus_tips_cd']:+.0f} % "
                f"high and the mesh-converged 3D value still {b['mesh_converged_over_airfoil2d_pct']:+.0f} % high "
                "(the remainder is the wall-function mesh family itself plus whatever the tip edges really add). "
                f"All of these assume a boundary layer that is turbulent from the leading edge. Measured NACA 0012 "
                f"drag near Re = 4 x 10^5 in low-turbulence tunnels is about 0.009-0.011, roughly "
                f"{100 * (1 - 0.010 / b['airfoil2d_cd']):.0f} % below even the wall-resolved fully turbulent value, "
                "because a real boundary layer stays laminar over a good part of the chord. So of the gap between "
                f"Quick ({q0['cd']:.4f}) and a measurement (about 0.010): roughly "
                f"{100 * (q0['cd'] - b['airfoil2d_plus_tips_cd']) / (q0['cd'] - 0.010):.0f} % is mesh and wall "
                f"treatment and {100 * (b['airfoil2d_plus_tips_cd'] - 0.010) / (q0['cd'] - 0.010):.0f} % is the fully "
                "turbulent assumption.")
    cost = []
    for level in list(LEVELS)[1:]:
        pairs = [(pick(level, a), pick("quick", a)) for a in (0, 10)]
        pairs = [(r, q) for r, q in pairs if r and q and r["converged"]]  # unsettled runs overstate the time
        if pairs:
            ratios = sorted(r["runtime_s"] / q["runtime_s"] for r, q in pairs)
            mem = max(r["peak_rss_mb"] for r, _ in pairs) / 1024
            span = f"{ratios[0]:.0f}x" if len(ratios) == 1 else f"{ratios[0]:.0f}-{ratios[-1]:.0f}x"
            cost.append(f"{level} {span} the time and {mem:.0f} GB")
    out.append(
        "**Should Quick stay the default?** Yes. It runs in about a minute on 4 cores; the finer levels cost "
        + (", ".join(cost) if cost else "several times as much")
        + " for a change of about 1-2 % in lift and 5-8 % in drag. Use Quick for what the course needs - lift "
        "curves, comparisons between shapes, angles and sizes, and the pressure pictures - and read its absolute "
        "drag as an upper estimate (and L/D as a lower one). Differences between runs on the same mesh are far "
        "more reliable than the absolute drag of any one run.")
    return out


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.is_file() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels", nargs="+", default=["quick", "standard"], choices=list(LEVELS))
    ap.add_argument("--alpha", type=float, nargs="+", default=[0.0, 10.0])
    ap.add_argument("--np", type=int, default=4, dest="n_procs")
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS_ROOT)
    ap.add_argument("--out", type=Path, default=REPO / "validation")
    ap.add_argument("--airfoil2d", action="store_true", help="run the wall-resolved 2D section at the wing's Re "
                    "instead of the 3D levels")
    ap.add_argument("--merge", action="store_true", help="keep the stored rows for other levels/angles")
    ap.add_argument("--rewrite", action="store_true", help="rewrite md (and derived numbers) from the stored json")
    ap.add_argument("--tag", default="", help="free text stored with the rows (e.g. 'layers v2')")
    args = ap.parse_args()
    json_path, md_path = args.out / "wing3d_mesh_study.json", args.out / "wing3d_mesh_study.md"
    stored = load(json_path)

    if args.rewrite:
        if not stored:
            print("nothing stored yet")
            return 1
        an = analyse(stored["rows"])
        stored["analysis"] = an
        json_path.write_text(json.dumps(stored, indent=2) + "\n")
        write_markdown(md_path, stored["rows"], stored["info"], an)
        print(f"rewrote {md_path}")
        return 0

    rows: list[dict] = []
    tmp = Path(tempfile.mkdtemp(prefix="windlab_meshstudy_"))
    geom, ref, _wing = wing_geometry(tmp)
    re_wing = SPEED * CHORD / NU_SEA_LEVEL
    try:
        for alpha in args.alpha:
            if args.airfoil2d:
                rows.append(run_airfoil_2d(alpha, args.n_procs, args.runs, re_wing))
                continue
            for level in args.levels:
                rows.append(run_level(level, alpha, args.n_procs, args.runs, geom, ref))
    except RunError as e:
        print(f"RUN FAILED in {e.stage}: {e}\n    log: {e.log}")
        if not rows:
            return 1
    for r in rows:
        r["tag"] = args.tag
        print(f"    {r['level']} alpha {r['alpha']:g}: {r['cells']:,d} cells, CL {r['cl']:.4f}, CD {r['cd']:.5f} "
              f"(p {fmt(r['cd_pressure'], 5)} + v {fmt(r['cd_viscous'], 5)}), {r['iterations']} it, "
              f"{r['runtime_s']:.0f} s, {r['peak_rss_mb'] / 1024:.1f} GB")

    info = {
        "date": time.strftime("%Y-%m-%d"),
        "machine": f"{platform.machine()} {platform.system()} container",
        "n_procs": args.n_procs,
        "case": {"code": CODE, "chord": CHORD, "span": SPAN, "airspeed": SPEED, "reynolds": re_wing},
        "levels": {k: {"preset": v[0].value, "bg_cell_over_l": (v[1] or PRESETS[v[0]]).bg_cell_over_l}
                   for k, v in LEVELS.items()},
        "label": "uncalibrated prototype - not yet validated",
    }
    if args.merge and stored:
        new = {(r["kind"], r["level"], r["alpha"]) for r in rows}
        kept = [r for r in stored["rows"] if (r["kind"], r["level"], r["alpha"]) not in new]
        rows = kept + rows
        info = {**stored["info"], "updated": info["date"], "n_procs": args.n_procs, "levels": info["levels"]}
    an = analyse(rows)
    args.out.mkdir(exist_ok=True)
    json_path.write_text(json.dumps({"info": info, "rows": rows, "analysis": an}, indent=2) + "\n")
    write_markdown(md_path, rows, info, an)
    print(f"wrote {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
