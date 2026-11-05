"""Report export (PRD RES-4): one finished run or sweep as a self-contained Markdown file and as a
printable HTML page (print to PDF from the browser).

Both carry the same content, built from what the run manager already has: the inputs (shape,
units, size, speed, angles, reference values), the mesh (preset, cells, y+, quality checks,
layer coverage), the convergence (iterations, stop reason, residuals), the results with their
expected-error ranges, charts as inline SVG drawn here in Python (no matplotlib), the pressure
and flow pictures as inline PNG (:mod:`windlab.app.raster`), the validation note, the
"uncalibrated prototype" banner, the WindLab version and commit, and the third-party notices.

No user text is ever written unescaped into the HTML; nothing in either file points at the
network (no external images, styles or scripts).
"""

from __future__ import annotations

import base64
import html
import json
import math
import time
from pathlib import Path

import numpy as np

from windlab import __version__, disclaimer, expected_error
from windlab.app import raster, surface
from windlab.app import slice as flow_slice
from windlab.app.case_export import GPL_NOTICE, NOTICE, TRADEMARK_ACKNOWLEDGEMENT, TRADEMARK_DISCLAIMER, windlab_commit
from windlab.app.runs import LABEL, PRESET_LABELS
from windlab.case_builder import EVERYDAY_CONVERGENCE, NU_SEA_LEVEL, RHO_SEA_LEVEL, Preset
from windlab.geometry.upload import UNIT_NAMES

PICTURE_WIDTH = 720

THIRD_PARTY = [
    ("OpenFOAM® (OpenCFD Ltd, v2606)", "GNU General Public License v3 (GPL-3.0)", "the flow solver, mesher and "
     "utilities run as separate programs; WindLab writes their input files and reads their output; OpenFOAM is "
     "distributed under the GNU GPL v3"),
    ("OpenCASCADE Technology (via cadquery-ocp)", "LGPL-2.1 with the Open CASCADE exception", "reads STEP files "
     "(optional)"),
    ("NumPy", "BSD 3-clause", "numbers and pictures"),
    ("FastAPI, Starlette, Pydantic, Uvicorn", "MIT / BSD", "the local web server"),
    ("three.js", "MIT", "the 3D views in the browser"),
    ("viridis colour map (matplotlib, CC0) and ColorBrewer RdBu (Apache 2.0)", "", "the colour scales"),
]

ACCURACY_NOTE = (
    "WindLab's flow solver was checked in 2D against NASA's NACA 0012 reference at Re 6 million: lift within 2 % and "
    "drag within 1 % of the reference. The everyday Quick mesh is much coarser: on the built-in NACA 0012 wing its "
    "lift is within a few per cent of a mesh ten times finer, but its drag is about 8-9 % too high, and about a "
    "third above the validated 2D value at the same Reynolds number, because the surface cells are coarse. WindLab "
    "also assumes the boundary layer is turbulent from the leading edge; at the Reynolds numbers of a student-size "
    "model (10^5 to 10^6) real air keeps a laminar run first, so measured drag is lower again, often by a third. "
    "Treat CD as an upper estimate and L/D as a lower one, and compare runs made on the same mesh with each other: "
    "trends and differences are far more trustworthy than absolute drag. The 'likely' ranges next to each "
    "coefficient come from WindLab's versioned validation table; 'unvalidated' means no measured or mesh-converged "
    "reference exists for that shape, Reynolds number or angle. On an uploaded part that is symmetric top to "
    "bottom, the lift at 0 degrees should be zero: what WindLab shows there is numerical noise from the mesh, not "
    "physics, below 1 % of the drag for smooth bodies. Thin fins or plates thinner than a mesh cell are not meshed "
    "symmetrically (a 42 cm rocket with 2 mm fins gave a sideways force as large as its drag on the Quick mesh), so "
    "WindLab measures the thickness of every upload and refines the mesh around such parts on its own; the 'Thin "
    "parts' row above says when it did (validation/robustness.md)."
)


# ----------------------------------------------------------------------------- small helpers


def _f(x, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    return f"{x:.{nd}g}"


def _len(m) -> str:
    if m is None:
        return "-"
    m = float(m)
    if m >= 1:
        return f"{m:.3g} m"
    if m >= 0.01:
        return f"{m * 100:.3g} cm"
    return f"{m * 1000:.3g} mm"


def _area(a) -> str:
    if a is None:
        return "-"
    a = float(a)
    if a >= 0.01:
        return f"{a:.3g} m²"
    if a >= 1e-4:
        return f"{a * 1e4:.3g} cm²"
    return f"{a * 1e6:.3g} mm²"


def _when(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t or time.time()))


def nice_ticks(lo: float, hi: float, n: int = 5) -> tuple[list[float], float]:
    span = (hi - lo) or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if span / (m * mag) <= n), 10 * mag)
    out, v = [], math.ceil(lo / step - 1e-9) * step
    while v <= hi + step * 1e-9:
        out.append(0.0 if abs(v) < step * 1e-9 else float(f"{v:.12g}"))
        v += step
    return out, step


def _tick_text(v: float, step: float) -> str:
    d = 0
    while d < 4 and abs(step * 10**d - round(step * 10**d)) > 1e-6 * 10**d:
        d += 1
    return f"{v:.{d}f}".replace("-", "−")


# ----------------------------------------------------------------------------- SVG charts

SERIES_COLOURS = ["#2a78d6", "#c2570c", "#2a8a4a", "#8e4fc9", "#a8225f"]


def svg_chart(series: list[dict], x_label: str, y_label: str, width: int = 560, height: int = 260,
              title: str | None = None, highlight: tuple | None = None) -> str:
    """A line / marker chart as an SVG string. ``series``: [{"points": [(x, y), ...], "name", "colour",
    "line": bool, "markers": bool}]. ``highlight``: (x, y, text) ringed on the chart."""
    pts_all = [(x, y) for s in series for x, y in s["points"] if x is not None and y is not None
               and math.isfinite(x) and math.isfinite(y)]
    pad_l, pad_r, pad_t, pad_b = 62, 16, 26 if title else 12, 44
    if not pts_all:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
                f'height="{height}" role="img" aria-label="{html.escape(x_label)} chart: no data">'
                f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" font-size="13">no data</text></svg>')
    xs, ys = [p[0] for p in pts_all], [p[1] for p in pts_all]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 - x0 <= 0:
        x0, x1 = x0 - 1, x1 + 1
    if y1 - y0 <= 0:
        y0, y1 = y0 - abs(y0) * 0.1 - 0.01, y1 + abs(y1) * 0.1 + 0.01
    yt, ystep = nice_ticks(y0, y1)
    xt, xstep = nice_ticks(x0, x1, 6)
    y0, y1 = min(y0, yt[0] if yt else y0), max(y1, yt[-1] if yt else y1)
    if yt and yt[0] > y0:
        yt.insert(0, yt[0] - ystep)
    if yt and yt[-1] < y1:
        yt.append(yt[-1] + ystep)
    y0, y1 = yt[0], yt[-1]
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b

    def X(x):
        return pad_l + (x - x0) / (x1 - x0) * pw

    def Y(y):
        return pad_t + (1 - (y - y0) / (y1 - y0)) * ph

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="img" aria-label="{html.escape(y_label)} against {html.escape(x_label)}" '
           'font-family="system-ui, sans-serif" font-size="12">',
           f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fcfcfb"/>']
    if title:
        out.append(f'<text x="{pad_l}" y="16" font-weight="600" font-size="13">{html.escape(title)}</text>')
    for v in yt:
        if y0 - 1e-12 <= v <= y1 + 1e-12:
            out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e1e0da"/>')
            out.append(f'<text x="{pad_l - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#474744">'
                       f'{_tick_text(v, ystep)}</text>')
    for v in xt:
        if x0 - 1e-12 <= v <= x1 + 1e-12:
            out.append(f'<line y1="{pad_t}" y2="{height - pad_b}" x1="{X(v):.1f}" x2="{X(v):.1f}" stroke="#e1e0da"/>')
            out.append(f'<text x="{X(v):.1f}" y="{height - pad_b + 16}" text-anchor="middle" fill="#474744">'
                       f'{_tick_text(v, xstep)}</text>')
    if y0 < 0 < y1:
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#8b8a85"/>')
    out.append(f'<rect x="{pad_l}" y="{pad_t}" width="{pw}" height="{ph}" fill="none" stroke="#8b8a85"/>')
    out.append(f'<text x="{pad_l + pw / 2:.1f}" y="{height - 8}" text-anchor="middle" fill="#474744">'
               f'{html.escape(x_label)}</text>')
    out.append(f'<text transform="translate(14 {pad_t + ph / 2:.1f}) rotate(-90)" text-anchor="middle" fill="#474744">'
               f'{html.escape(y_label)}</text>')
    for k, s in enumerate(series):
        colour = s.get("colour") or SERIES_COLOURS[k % len(SERIES_COLOURS)]
        pts = [(X(x), Y(y)) for x, y in s["points"] if x is not None and y is not None]
        if s.get("line", True) and len(pts) > 1:
            d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
            out.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.8"/>')
        if s.get("markers", False):
            for x, y in pts:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{colour}"/>')
        if len(series) > 1 and s.get("name"):
            out.append(f'<text x="{width - pad_r - 6}" y="{pad_t + 14 + 15 * k}" text-anchor="end" fill="{colour}" '
                       f'font-weight="600">{html.escape(str(s["name"]))}</text>')
    if highlight and highlight[0] is not None and highlight[1] is not None:
        hx, hy, text = X(highlight[0]), Y(highlight[1]), highlight[2]
        out.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="7" fill="none" stroke="#e8590c" stroke-width="2"/>')
        out.append(f'<text x="{hx + 10:.1f}" y="{hy - 8:.1f}" fill="#e8590c" font-weight="600">'
                   f'{html.escape(text)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_legend(lo: float, hi: float, stops: list[str], title: str, width: int = 360) -> str:
    """A horizontal colour bar with ticks (for the pictures)."""
    gid = f"g{abs(hash((lo, hi, title))) % 10**8}"
    ticks, step = nice_ticks(lo, hi, 5)
    ticks = [t for t in ticks if lo - 1e-9 <= t <= hi + 1e-9]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 46" width="{width}" height="46" role="img" '
           f'aria-label="colour scale: {html.escape(title)} from {_f(lo, 3)} to {_f(hi, 3)}" '
           'font-family="system-ui, sans-serif" font-size="11">', "<defs>",
           f'<linearGradient id="{gid}" x1="0" x2="1" y1="0" y2="0">']
    for i, s in enumerate(stops):
        out.append(f'<stop offset="{100 * i / (len(stops) - 1):.1f}%" stop-color="{s}"/>')
    out += ["</linearGradient>", "</defs>",
            f'<text x="0" y="11" font-weight="600" fill="#1b1b1a">{html.escape(title)}</text>',
            f'<rect x="0" y="16" width="{width}" height="12" fill="url(#{gid})" stroke="#8b8a85"/>']
    for t in ticks:
        x = (t - lo) / (hi - lo) * width if hi > lo else 0
        anchor = "start" if x < 20 else ("end" if x > width - 20 else "middle")
        out.append(f'<text x="{x:.1f}" y="42" text-anchor="{anchor}" fill="#474744">{_tick_text(t, step)}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ----------------------------------------------------------------------------- gathering


def _read_json(path: Path) -> dict | None:
    try:
        if path.is_file() and not path.is_symlink():
            return json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return None


def stop_reason(result: dict, params: dict, point: dict | None = None) -> str:
    """Why the solver stopped, in plain words."""
    r = point or result
    conv = EVERYDAY_CONVERGENCE
    cap = params.get("max_iterations")
    if r.get("converged"):
        return (f"stopped by WindLab's settling rule after {r.get('iterations')} iterations: the means of CL and CD "
                f"over the last {conv['window']} iterations agreed with the means {conv['lag']} iterations earlier "
                f"within {conv['cl_tol'] * 100:g} % (CL) and {conv['cd_tol'] * 100:g} % (CD), with the solver "
                f"residuals below {conv['residual_tol']:g}")
    if r.get("status") == "failed":
        return "the airflow calculation became unstable and did not finish at this angle"
    return (f"reached the iteration cap ({cap}) without settling: the numbers are a rough guide (an unsteady wake "
            "or stalled flow does not settle in a steady solver)")


def collect(st, status: dict, geometry_meta: dict | None, case_dir: Path, commit: str | None = None,
            pictures: bool = True) -> dict:
    """Everything the two renderers need, as plain data (pictures as PNG bytes)."""
    p, r = status.get("params") or {}, status.get("result") or {}
    sweep = p.get("kind") == "sweep"
    case_meta = _read_json(case_dir / "windlab_case.json") or {}
    report = _read_json(case_dir / "windlab_report.json") or {}
    mesh = report.get("mesh") or {}
    g = geometry_meta or {}
    greport = g.get("report") or {}
    source = greport.get("source") or {}
    q = 0.5 * RHO_SEA_LEVEL * float(p.get("airspeed") or 0) ** 2 * float(p.get("reference_area") or 0)
    ctx: dict = {
        "run_id": status.get("run_id"), "kind": "sweep" if sweep else "single", "status": status.get("status"),
        "created": _when(status.get("created")), "finished": _when(status.get("finished")),
        "generated": _when(None), "version": __version__, "commit": commit or windlab_commit(), "label": LABEL,
        "shape": {
            "name": p.get("geometry_name") or "-",
            "kind": {"naca": "built-in NACA wing", "builtin": "built-in body", "upload": "uploaded part"}.get(
                g.get("kind"), "-"),
            "class": p.get("geometry_class") or "-",
            "units": UNIT_NAMES.get(greport.get("units"), greport.get("units")) if g.get("kind") == "upload" else None,
            "format": greport.get("format"),
            "size_m": greport.get("size_m") or (list(np.subtract(case_meta["body_max"], case_meta["body_min"]))
                                                if "body_max" in case_meta else None),
            "triangles": greport.get("triangles"),
            "step": source or None,
            "orientation": g.get("orientation"),
        },
        "conditions": {
            "airspeed": p.get("airspeed"), "alpha_deg": p.get("alpha_deg"), "angles": p.get("angles"),
            "reynolds": p.get("reynolds"), "mach": p.get("mach"), "rho": RHO_SEA_LEVEL, "nu": NU_SEA_LEVEL,
            "reference_area": p.get("reference_area"), "reference_length": p.get("reference_length"),
            "reference_how": p.get("reference_how"), "moment_point": case_meta.get("cofr"), "dynamic_force": q,
            "part_type": p.get("part_type"), "area_method": p.get("area_method"),
        },
        "mesh": {
            "preset": p.get("preset", "quick"),
            "preset_label": PRESET_LABELS.get(Preset(p.get("preset", "quick")), (p.get("preset"),))[0]
            if p.get("preset", "quick") in {x.value for x in Preset} else p.get("preset"),
            "cells": r.get("cells") or mesh.get("cells"), "reused": bool(r.get("mesh_reused")),
            "max_non_orthogonality": mesh.get("max_non_orthogonality"), "max_skewness": mesh.get("max_skewness"),
            "failed_checks": mesh.get("failed_checks"), "mesh_ok": mesh.get("mesh_ok"),
            "layers": (mesh.get("layers") or {}).get("body"),
            "y_plus": (report.get("y_plus") or {}).get("body"),
            "background_cell": case_meta.get("background_cell"), "surface_cell": case_meta.get("surface_cell"),
            "n_layers": case_meta.get("n_layers"), "n_procs": p.get("n_procs"),
            "domain": [case_meta.get("domain_min"), case_meta.get("domain_max")],
            "thin_features": p.get("thin_features") or case_meta.get("thin_features"),
        },
        "convergence": {
            "iterations": r.get("iterations"), "converged": r.get("converged"),
            "max_iterations": p.get("max_iterations"), "reason": stop_reason(r, p) if not sweep else None,
            "residuals": report.get("residuals") or {},
            "solver_seconds": r.get("solver_seconds"), "peak_memory_mb": r.get("peak_memory_mb"),
            "warm_start": r.get("warm_start"),
            "stage_seconds": {k: round(float(v), 1) for k, v in (status.get("stage_times") or {}).items()},
        },
        "warnings": list(status.get("warnings") or []),
        "validation_table": expected_error.table_version(),
        "accuracy_note": ACCURACY_NOTE,
        "third_party": THIRD_PARTY,
        "notice": NOTICE,
        "trademark": f"{TRADEMARK_ACKNOWLEDGEMENT} {TRADEMARK_DISCLAIMER} {GPL_NOTICE}",
        "charts": [],
        "pictures": [],
    }
    coeffs = report.get("coefficients") or {}
    if sweep:
        pts = []
        for pt in r.get("points") or status.get("points") or []:
            pts.append({**pt, "reason": stop_reason(r, p, pt)})
        ctx["results"] = {"points": pts, "max_l_over_d": r.get("max_l_over_d"), "max_l_over_d_alpha":
                          r.get("max_l_over_d_alpha"), "cl_max": r.get("cl_max"), "cl_max_alpha": r.get("cl_max_alpha"),
                          "stall_alpha": r.get("stall_alpha"), "stall_note": r.get("stall_note")}
        done = [pt for pt in pts if pt.get("status") == "done"]
        best = r.get("max_l_over_d_alpha")
        hl = next(((pt["alpha_deg"], pt.get("l_over_d"), f"best L/D at {best:g}°") for pt in done
                   if pt["alpha_deg"] == best), None)
        ctx["charts"] = [
            {"title": "Lift coefficient against angle of attack",
             "svg": svg_chart([{"points": [(pt["alpha_deg"], pt["cl"]) for pt in done], "markers": True}],
                              "angle of attack (°)", "CL", title="CL against angle of attack",
                              highlight=next(((pt["alpha_deg"], pt["cl"], "lift stops rising") for pt in done
                                              if pt["alpha_deg"] == r.get("stall_alpha")), None))},
            {"title": "Drag polar: CL against CD",
             "svg": svg_chart([{"points": [(pt["cd"], pt["cl"]) for pt in done], "markers": True}], "CD", "CL",
                              title="Drag polar")},
            {"title": "Lift-to-drag ratio against angle of attack",
             "svg": svg_chart([{"points": [(pt["alpha_deg"], pt["l_over_d"]) for pt in done], "markers": True}],
                              "angle of attack (°)", "L/D", title="L/D against angle of attack", highlight=hl)},
        ]
    else:
        ctx["results"] = {
            "cl": r.get("cl"), "cd": r.get("cd"), "cm": r.get("cm"), "l_over_d": r.get("l_over_d"),
            "cl_pm": r.get("cl_pm"), "cd_pm": r.get("cd_pm"), "lift_n": None if r.get("cl") is None else q * r["cl"],
            "drag_n": None if r.get("cd") is None else q * r["cd"], "expected": r.get("expected") or {},
            "cd_pressure": coeffs.get("cd_pressure"), "cd_viscous": coeffs.get("cd_viscous"),
            "cl_pressure": coeffs.get("cl_pressure"), "cl_viscous": coeffs.get("cl_viscous"),
        }
        hist = status.get("history") or []
        ctx["charts"] = [
            {"title": "Lift coefficient while the solver worked",
             "svg": svg_chart([{"points": [(h[0], h[1]) for h in hist]}], "solver iteration", "CL",
                              title="CL against iteration")},
            {"title": "Drag coefficient while the solver worked",
             "svg": svg_chart([{"points": [(h[0], h[2]) for h in hist]}], "solver iteration", "CD",
                              title="CD against iteration")},
        ]
    if pictures:
        ctx["pictures"] = _pictures(case_dir, p, r, status)
    return ctx


def _pictures(case_dir: Path, p: dict, r: dict, status: dict) -> list[dict]:
    """The pressure surface and the flow slice as PNG (the best-L/D angle of a sweep)."""
    sweep = p.get("kind") == "sweep"
    suffix, alpha = "", p.get("alpha_deg")
    if sweep:
        best = r.get("max_l_over_d_alpha")
        pts = status.get("points") or []
        idx = next((i for i, pt in enumerate(pts) if pt.get("alpha_deg") == best), 0)
        suffix, alpha = f"_{idx}", pts[idx].get("alpha_deg") if pts else None
    out = []
    surf_f = case_dir / f"windlab_surface{suffix}.bin"
    if surf_f.is_file() and not surf_f.is_symlink():
        try:
            d = surface.decode(surf_f.read_bytes())
            pic = raster.render_surface(d, PICTURE_WIDTH)
            out.append({"id": "surface", "png": pic["png"], "width": pic["width"], "height": pic["height"],
                        "caption": f"Pressure coefficient Cp on the part at {_f(alpha, 3)}°, seen from the front, "
                                   f"left and above (wind along x). Blue = suction, red = pressure; Cp goes from "
                                   f"{_f(d['cp_min'], 3)} to {_f(d['cp_max'], 3)}.",
                        "legend": svg_legend(pic["lo"], pic["hi"], raster.DIVERGING, "pressure coefficient Cp")})
        except (surface.SurfaceError, ValueError, OSError):
            pass
    slice_f = case_dir / f"windlab_slice{suffix}.bin"
    if slice_f.is_file() and not slice_f.is_symlink():
        try:
            sl = flow_slice.decode(slice_f.read_bytes())
            info = _read_json(case_dir / f"windlab_sliceinfo{suffix}.json") or {}
            win = (info.get("windows") or {}).get("closeup")
            lines = ((info.get("streamlines") or {}).get("closeup")) or None
            pic = raster.render_slice(sl, win, PICTURE_WIDTH, streamlines=lines)
            out.append({"id": "slice", "png": pic["png"], "width": pic["width"], "height": pic["height"],
                        "caption": f"Air speed (relative to the wind speed) in a cut through the middle of the part at "
                                   f"{_f(alpha, 3)}°, seen from the side; the wind blows from left to right, the dark "
                                   f"shape is the part" + (", the thin lines are streamlines" if lines else "") + ".",
                        "legend": svg_legend(pic["lo"], pic["hi"], raster.VIRIDIS, "air speed ÷ wind speed")})
        except (surface.SurfaceError, ValueError, OSError, KeyError):
            pass
    return out


# ----------------------------------------------------------------------------- rows shared by both formats


def _input_rows(ctx: dict) -> list[tuple[str, str]]:
    s, c = ctx["shape"], ctx["conditions"]
    rows = [("Shape", f"{s['name']} ({s['kind']}; class for the expected-error table: {s['class']})")]
    if s.get("size_m"):
        lx, ly, lz = s["size_m"]
        rows.append(("Size (x along the wind, y across, z up)", f"{_len(lx)} × {_len(ly)} × {_len(lz)}"))
    if s.get("units"):
        fmt = f" ({s['format'].upper()} file)" if s.get("format") else ""
        rows.append(("Units of the uploaded file", f"{s['units']}{fmt}"))
    if s.get("step"):
        st = s["step"]
        rows.append(("STEP file", f"{st.get('solids')} solid(s), {st.get('faces')} faces, unit declared in the file: "
                                  f"{UNIT_NAMES.get(st.get('unit_declared'), st.get('unit_declared') or 'none')}"
                     + ("; touching solids fused into one" if st.get("fused") else "")
                     + ("; small defects healed" if st.get("healed") else "")
                     + (f"; {st.get('bodies_used')} of {st.get('bodies_available')} separate bodies used"
                        if (st.get("bodies_available") or 0) > 1 else "")))
    if s.get("triangles"):
        rows.append(("Surface triangles", f"{s['triangles']:,d}"))
    rows.append(("Airspeed", f"{_f(c['airspeed'], 4)} m/s"))
    if ctx["kind"] == "sweep":
        rows.append(("Angles of attack", ", ".join(f"{a:g}°" for a in c["angles"] or []) + " (one mesh, one solve per "
                                                                                           "angle)"))
    else:
        rows.append(("Angle of attack", f"{_f(c['alpha_deg'], 4)}°"))
    rows += [
        ("Reynolds number", f"{_f(c['reynolds'], 4)} (on the reference length)"),
        ("Mach number", f"{_f(c['mach'], 3)} (incompressible flow assumed; valid below about 0.3)"),
        ("Reference area and length", f"{_area(c['reference_area'])} and {_len(c['reference_length'])} "
                                      f"({c['reference_how'] or '-'})"),
    ]
    if s["kind"] == "uploaded part" and c.get("part_type") in ("wing", "body"):
        measured = ("measured from the part's surface" if c.get("area_method") == "silhouette"
                    else "from the box around the part")
        rows.append(("Part treated as", "a body: CL and CD on its frontal area (the silhouette seen from the front, "
                                        f"{measured}), as drag coefficients of cars, rockets and spheres are quoted"
                     if c["part_type"] == "body" else
                     "a wing: CL and CD on its planform area (the outline seen from above, "
                     f"{measured}), as wing coefficients are quoted"))
    rows += [
        ("Moment reference point", "(" + ", ".join(_len(v) for v in c["moment_point"]) + ")" if c.get("moment_point")
         else "-"),
        ("Air", f"sea level, 15 °C: density {c['rho']:g} kg/m³, kinematic viscosity {c['nu']:g} m²/s; dynamic "
                f"pressure × reference area = {_f(c['dynamic_force'], 4)} N"),
    ]
    return rows


def _mesh_rows(ctx: dict) -> list[tuple[str, str]]:
    m = ctx["mesh"]
    rows = [("Mesh preset", f"{m['preset_label']} ({m['preset']})" + (" - reused from an earlier run of this shape"
                                                                      if m["reused"] else "")),
            ("Cells", f"{m['cells']:,d}" if m.get("cells") else "-")]
    if m.get("background_cell"):
        rows.append(("Cell sizes", f"background {_len(m['background_cell'])}, on the surface "
                                   f"{_len(m['surface_cell'])}, {m['n_layers']} boundary-layer cells (snappyHexMesh)"))
    tf = m.get("thin_features")
    if tf:
        share = f"{tf['area_share'] * 100:.0f} % of the surface"
        found = f"plates or fins about {_len(tf['thickness'])} thick ({share}) were found; "
        budget = f"{int(tf.get('cell_budget') or 0):,d}"
        full = f"{int(tf.get('full_added_cells') or 0):,d}"
        if tf.get("extra_levels", 1) == 0:
            rows.append(("Thin parts", found + "the refinement band around them was left out because it would have "
                                       f"added about {full} cells, more than the {budget}-cell allowance for it "
                                       "(task 014 cell budget); results near them may be rough"))
        else:
            rows.append(("Thin parts", found + f"the mesh was refined around them to {_len(tf['refined_cell'])} cells "
                                       "(a refinement band of snappyHexMesh"
                                       + (f"; one level less than their thickness calls for, to stay within the "
                                          f"{budget}-cell allowance: the full band would have added about {full} "
                                          "cells" if tf.get("capped") else "") + ")"
                                       + (", which still leaves them rough" if tf.get("rough") else "")))
    yp = m.get("y_plus")
    if yp:
        rows.append(("y⁺ on the part", f"min {_f(yp.get('min'), 3)}, mean {_f(yp.get('average'), 3)}, "
                                       f"max {_f(yp.get('max'), 3)} (wall functions; the k-ω SST model is used "
                                       "fully turbulent)"))
    if m.get("failed_checks") is not None:
        rows.append(("Quality checks (checkMesh)", "all passed" if m.get("mesh_ok") else
                     f"{m['failed_checks']} check(s) failed; results may be less accurate"))
    if m.get("max_non_orthogonality") is not None:
        rows.append(("Maximum non-orthogonality", f"{_f(m['max_non_orthogonality'], 3)}°"
                                                  + (f", maximum skewness {_f(m['max_skewness'], 3)}"
                                                     if m.get("max_skewness") is not None else "")))
    lay = m.get("layers")
    if lay:
        rows.append(("Boundary layers", f"{_f(lay.get('mean_layers'), 3)} of {lay.get('target_layers')} layers on "
                                        f"average, {_f(lay.get('thickness_pct'), 3)} % of the planned thickness"))
    rows.append(("Processor cores", f"{m.get('n_procs') or '-'}"))
    return rows


def _convergence_rows(ctx: dict) -> list[tuple[str, str]]:
    c = ctx["convergence"]
    rows = []
    if ctx["kind"] == "single":
        rows.append(("Solver iterations", f"{c['iterations']} of at most {c['max_iterations']}"))
        rows.append(("Why it stopped", c["reason"] or "-"))
    else:
        rows.append(("Solver iterations", f"{c['iterations']} over all angles, at most {c['max_iterations']} per "
                                          "angle; each angle starts from the previous one"))
    if c.get("warm_start"):
        rows.append(("Start", f"from your earlier result at {_f(c['warm_start'].get('alpha_deg'), 3)}°"))
    if c.get("residuals"):
        rows.append(("Final residuals (initial, last iteration)",
                     ", ".join(f"{k} {v:.1e}" for k, v in sorted(c["residuals"].items()))))
    stages = c.get("stage_seconds") or {}
    if stages:
        rows.append(("Time per stage", ", ".join(f"{k} {v:g} s" for k, v in stages.items()
                                                 if k not in ("done", "failed", "cancelled", "queued"))))
    rows.append(("Solver time, peak memory", f"{_f(c['solver_seconds'], 3)} s, {_f(c['peak_memory_mb'], 3)} MB"))
    return rows


def _range_text(exp: dict | None) -> str:
    if not exp:
        return "-"
    return exp.get("text") or "-"


def _cell_fmt(done: bool):
    """A number formatter for one sweep row: the value when the angle finished, else '-'."""
    return (lambda x, nd=4: _f(x, nd)) if done else (lambda x, nd=4: "-")


# ----------------------------------------------------------------------------- Markdown


def markdown(ctx: dict) -> str:
    s, c, res = ctx["shape"], ctx["conditions"], ctx["results"]
    L = [f"# WindLab report: {s['name']}", "",
         f"> **{ctx['label']}.** Use the numbers to learn and compare, not to design real hardware.", "",
         f"{'Angle sweep' if ctx['kind'] == 'sweep' else 'Single run'} made {ctx['created']} (finished "
         f"{ctx['finished']}); report written {ctx['generated']} by SenSym WindLab {ctx['version']} "
         f"(commit {ctx['commit']}). Run id `{ctx['run_id']}`.", ""]

    def table(rows):
        L.append("| | |")
        L.append("|:--|:--|")
        for k, v in rows:
            L.append(f"| {k} | {_md(v)} |")
        L.append("")

    L += ["## Inputs", ""]
    table(_input_rows(ctx))
    L += ["## Mesh", ""]
    table(_mesh_rows(ctx))
    L += ["## Convergence", ""]
    table(_convergence_rows(ctx))
    L += ["## Results", ""]
    if ctx["kind"] == "sweep":
        L += ["| angle (°) | CL | CL likely | CD | CD likely | CM | L/D | iterations | settled | why it stopped |",
              "|---:|---:|:--|---:|:--|---:|---:|---:|:--|:--|"]
        for pt in res["points"]:
            ok = pt.get("status") == "done"
            e = pt.get("expected") or {}
            v = _cell_fmt(ok)
            L.append(f"| {pt.get('alpha_deg'):g} | {v(pt.get('cl'))} | {_range_text(e.get('cl'))} | "
                     f"{v(pt.get('cd'))} | {_range_text(e.get('cd'))} | {v(pt.get('cm'))} | "
                     f"{v(pt.get('l_over_d'), 3)} | {pt.get('iterations') or '-'} | "
                     f"{'yes' if pt.get('converged') else ('no' if ok else '-')} | {_md(pt.get('reason', ''))} |")
        L.append("")
        if res.get("max_l_over_d") is not None:
            L.append(f"The best lift-to-drag ratio, L/D = {_f(res['max_l_over_d'], 3)}, is at "
                     f"{res['max_l_over_d_alpha']:g}°.")
        if res.get("stall_note"):
            L.append(res["stall_note"].replace("C_L", "CL"))
        L.append("")
    else:
        e = res["expected"]
        L += ["| coefficient | value | likely range (true value) | wobble over the last iterations |",
              "|:--|---:|:--|:--|",
              f"| Lift coefficient CL | {_f(res['cl'])} | {_range_text(e.get('cl'))} | ± {_f(res['cl_pm'])} |",
              f"| Drag coefficient CD | {_f(res['cd'])} | {_range_text(e.get('cd'))} | ± {_f(res['cd_pm'])} |",
              f"| Lift-to-drag ratio L/D | {_f(res['l_over_d'], 3)} | {_range_text(e.get('l_over_d'))} | |",
              f"| Pitching moment coefficient CM | {_f(res['cm'])} | - | |", ""]
        if res.get("cd_pressure") is not None:
            L.append(f"Drag split: pressure {_f(res['cd_pressure'])}, viscous (skin friction) {_f(res['cd_viscous'])}.")
        L.append(f"With the reference area of {_area(c['reference_area'])} that is about {_f(res['lift_n'], 3)} N of "
                 f"lift and {_f(res['drag_n'], 3)} N of drag at {_f(c['airspeed'], 3)} m/s.")
        L.append("")
        why = [x.get("why") for x in (e.get("cl"), e.get("cd")) if x and x.get("why")]
        if why:
            L += ["**Why these ranges?** " + " ".join(dict.fromkeys(why)), ""]
    if ctx["warnings"]:
        L += ["### Notes from the run", ""] + [f"- {w}" for w in ctx["warnings"]] + [""]
    if ctx["charts"]:
        L += ["## Charts", ""]
        for ch in ctx["charts"]:
            L += [f"**{ch['title']}**", "", ch["svg"], ""]
    if ctx["pictures"]:
        L += ["## Pictures", ""]
        for pic in ctx["pictures"]:
            uri = "data:image/png;base64," + base64.b64encode(pic["png"]).decode()
            L += [f"![{_md(pic['caption'])}]({uri})", "", pic["legend"], "", pic["caption"], ""]
    L += ["## How much can these numbers be trusted?", "", ctx["accuracy_note"],
          f" Validation table version {ctx['validation_table']}; evidence in the `validation/` folder of the WindLab "
          "repository.", "",
          "## Third-party software and trade marks", "",
          "WindLab is open-source software by SenSym LLC for educational and preliminary design use; not for "
          "airworthiness or safety determinations.", ""]
    for name, lic, what in ctx["third_party"]:
        L.append(f"- **{name}**" + (f" ({lic})" if lic else "") + f": {what}.")
    L += ["", ctx["trademark"], "", "```", ctx["notice"].strip(), "```", "", "---", "", f"*{disclaimer.FOOTER}*", ""]
    return "\n".join(L)


def _md(text) -> str:
    """One table cell: no pipes or line breaks."""
    return " ".join(str(text).replace("|", "¦").split())


# ----------------------------------------------------------------------------- HTML

CSS = """
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif; color: #1b1b1a;
  max-width: 900px; margin: 1.5rem auto; padding: 0 1rem; line-height: 1.45; }
h1 { font-size: 1.6rem; margin-bottom: .2rem; }
h2 { font-size: 1.2rem; margin-top: 1.6rem; border-bottom: 1px solid #d9d8d3; }
.banner { background: #fff4d6; color: #5c3d00; border-left: 5px solid #c98500; padding: .5rem .8rem;
  border-radius: 4px; }
.meta { color: #474744; font-size: .92rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0; font-size: .95rem; }
th, td { border: 1px solid #d9d8d3; padding: .3rem .5rem; text-align: left; vertical-align: top; }
th { background: #f2f1ed; } td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.kv th { width: 30%; font-weight: 600; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; }
figure { margin: 1rem 0; } figure svg { max-width: 100%; height: auto; }
figure img { max-width: 100%; height: auto; border: 1px solid #d9d8d3; }
figcaption { color: #474744; font-size: .92rem; }
.unvalidated { color: #5c3d00; background: #fff4d6; border-radius: 3px; padding: 0 .3rem; }
.print-hint { color: #474744; font-size: .9rem; border: 1px dashed #8b8a85; padding: .4rem .6rem; }
.trademark { font-size: 1rem; }
.disclaimer { margin-top: 1.2rem; border-top: 1px solid #d9d8d3; padding-top: .5rem; color: #5c3d00; font-weight: 600; }
pre { white-space: pre-wrap; font-size: .85rem; background: #f2f1ed; padding: .6rem; }
@media print { .print-hint { display: none; } body { max-width: none; margin: 0; } h2 { page-break-after: avoid; }
  figure, table { page-break-inside: avoid; } }
"""


def html_page(ctx: dict) -> str:
    e = html.escape
    s, c, res = ctx["shape"], ctx["conditions"], ctx["results"]
    H = [f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
         f"content=\"width=device-width, initial-scale=1\"><title>WindLab report: {e(s['name'])}</title>"
         f"<style>{CSS}</style></head><body>",
         f"<h1>WindLab report: {e(s['name'])}</h1>",
         f"<p class=\"banner\"><strong>{e(ctx['label'])}.</strong> Use the numbers to learn and compare, not to design "
         "real hardware.</p>",
         "<p class=\"print-hint\">To keep this report as a PDF, print this page (Ctrl+P or Cmd+P) and choose "
         "\"Save as PDF\".</p>",
         f"<p class=\"meta\">{'Angle sweep' if ctx['kind'] == 'sweep' else 'Single run'} made {e(ctx['created'])} "
         f"(finished {e(ctx['finished'])}); report written {e(ctx['generated'])} by SenSym WindLab {e(ctx['version'])} "
         f"(commit {e(ctx['commit'])}). Run id <code>{e(str(ctx['run_id']))}</code>.</p>"]

    def kv(rows):
        H.append("<table class=\"kv\">")
        for k, v in rows:
            H.append(f"<tr><th scope=\"row\">{e(k)}</th><td>{e(str(v))}</td></tr>")
        H.append("</table>")

    def rng(x):
        if not x:
            return "-"
        cls = ' class="unvalidated"' if x.get("status") == "unvalidated" else ""
        return f"<span{cls}>{e(x.get('text') or '-')}</span>"

    H.append("<h2>Inputs</h2>")
    kv(_input_rows(ctx))
    H.append("<h2>Mesh</h2>")
    kv(_mesh_rows(ctx))
    H.append("<h2>Convergence</h2>")
    kv(_convergence_rows(ctx))
    H.append("<h2>Results</h2>")
    if ctx["kind"] == "sweep":
        H.append("<table><thead><tr><th class=\"num\">angle (°)</th><th class=\"num\">CL</th><th>CL likely</th>"
                 "<th class=\"num\">CD</th><th>CD likely</th><th class=\"num\">CM</th><th class=\"num\">L/D</th>"
                 "<th class=\"num\">iterations</th><th>settled</th><th>why it stopped</th></tr></thead><tbody>")
        for pt in res["points"]:
            ok = pt.get("status") == "done"
            ex = pt.get("expected") or {}
            v = _cell_fmt(ok)
            H.append(f"<tr><td class=\"num\">{pt.get('alpha_deg'):g}</td><td class=\"num\">{v(pt.get('cl'))}</td>"
                     f"<td>{rng(ex.get('cl'))}</td><td class=\"num\">{v(pt.get('cd'))}</td>"
                     f"<td>{rng(ex.get('cd'))}</td><td class=\"num\">{v(pt.get('cm'))}</td>"
                     f"<td class=\"num\">{v(pt.get('l_over_d'), 3)}</td>"
                     f"<td class=\"num\">{pt.get('iterations') or '-'}</td>"
                     f"<td>{'yes' if pt.get('converged') else ('no' if ok else '-')}</td>"
                     f"<td>{e(pt.get('reason', ''))}</td></tr>")
        H.append("</tbody></table>")
        if res.get("max_l_over_d") is not None:
            H.append(f"<p>The best lift-to-drag ratio, L/D = {_f(res['max_l_over_d'], 3)}, is at "
                     f"{res['max_l_over_d_alpha']:g}°.</p>")
        if res.get("stall_note"):
            H.append(f"<p>{e(res['stall_note'].replace('C_L', 'CL'))}</p>")
    else:
        ex = res["expected"]
        H.append("<table><thead><tr><th>coefficient</th><th class=\"num\">value</th><th>likely range (true value)</th>"
                 "<th>wobble over the last iterations</th></tr></thead><tbody>"
                 f"<tr><td>Lift coefficient CL</td><td class=\"num\">{_f(res['cl'])}</td><td>{rng(ex.get('cl'))}</td>"
                 f"<td>± {_f(res['cl_pm'])}</td></tr>"
                 f"<tr><td>Drag coefficient CD</td><td class=\"num\">{_f(res['cd'])}</td><td>{rng(ex.get('cd'))}</td>"
                 f"<td>± {_f(res['cd_pm'])}</td></tr>"
                 f"<tr><td>Lift-to-drag ratio L/D</td><td class=\"num\">{_f(res['l_over_d'], 3)}</td>"
                 f"<td>{rng(ex.get('l_over_d'))}</td><td></td></tr>"
                 f"<tr><td>Pitching moment coefficient CM</td><td class=\"num\">{_f(res['cm'])}</td><td>-</td>"
                 "<td></td></tr></tbody></table>")
        if res.get("cd_pressure") is not None:
            H.append(f"<p>Drag split: pressure {_f(res['cd_pressure'])}, viscous (skin friction) "
                     f"{_f(res['cd_viscous'])}.</p>")
        H.append(f"<p>With the reference area of {e(_area(c['reference_area']))} that is about "
                 f"{_f(res['lift_n'], 3)} N of lift and {_f(res['drag_n'], 3)} N of drag at {_f(c['airspeed'], 3)} m/s."
                 "</p>")
        why = [x.get("why") for x in (ex.get("cl"), ex.get("cd")) if x and x.get("why")]
        if why:
            H.append("<p><strong>Why these ranges?</strong> " + e(" ".join(dict.fromkeys(why))) + "</p>")
    if ctx["warnings"]:
        H.append("<h3>Notes from the run</h3><ul>" + "".join(f"<li>{e(w)}</li>" for w in ctx["warnings"]) + "</ul>")
    if ctx["charts"]:
        H.append("<h2>Charts</h2><div class=\"charts\">")
        for ch in ctx["charts"]:
            H.append(f"<figure>{ch['svg']}<figcaption>{e(ch['title'])}</figcaption></figure>")
        H.append("</div>")
    if ctx["pictures"]:
        H.append("<h2>Pictures</h2>")
        for pic in ctx["pictures"]:
            uri = "data:image/png;base64," + base64.b64encode(pic["png"]).decode()
            H.append(f"<figure><img src=\"{uri}\" width=\"{pic['width']}\" height=\"{pic['height']}\" "
                     f"alt=\"{e(pic['caption'])}\">{pic['legend']}<figcaption>{e(pic['caption'])}</figcaption></figure>")
    H.append("<h2>How much can these numbers be trusted?</h2>")
    H.append(f"<p>{e(ctx['accuracy_note'])} Validation table version {e(ctx['validation_table'])}; evidence in the "
             "<code>validation/</code> folder of the WindLab repository.</p>")
    H.append("<h2>Third-party software and trade marks</h2><p>WindLab is open-source software by SenSym LLC for "
             "educational and preliminary design use; not for airworthiness or safety determinations.</p><ul>")
    for name, lic, what in ctx["third_party"]:
        H.append(f"<li><strong>{e(name)}</strong>" + (f" ({e(lic)})" if lic else "") + f": {e(what)}.</li>")
    H.append("</ul>")
    H.append(f"<p class=\"trademark\">{e(ctx['trademark'])}</p>")
    H.append(f"<pre>{e(ctx['notice'].strip())}</pre>")
    H.append(f"<footer class=\"disclaimer\"><p>{e(disclaimer.FOOTER)}</p></footer>")
    H.append("</body></html>")
    return "\n".join(H)


def file_name(status: dict, ext: str) -> str:
    day = time.strftime("%Y-%m-%d", time.localtime(status.get("created") or time.time()))
    kind = (status.get("params") or {}).get("kind", "single")
    return f"windlab-report-{kind}-{day}-{str(status.get('run_id', 'run'))[:8]}.{ext}"


__all__ = ["ACCURACY_NOTE", "THIRD_PARTY", "collect", "file_name", "html_page", "markdown", "stop_reason", "svg_chart",
           "svg_legend"]
