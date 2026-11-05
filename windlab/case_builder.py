"""Build an OpenFOAM case directory from the external_aero template.

Security model (PRD SEC-2 / SEC-6 / SEC-7):

* Every value written into an OpenFOAM dictionary is a number computed here from
  validated inputs. No user text is ever written into a dictionary.
* Templates use ``@@NAME@@`` placeholders; rendering fails if any is left unfilled,
  and the output is scanned for dangerous directives (``#codeStream``, ``#calc``,
  ``coded``, ``systemCall``, any ``#include`` other than OpenFOAM's own ``#includeEtc``).
* Run directories are named by a server-generated UUID4 and safe-joined under the
  runs root; derived values (cells, iterations, processor count) are clamped.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from windlab.geometry import orient, thin
from windlab.geometry.stl import write_binary_stl

TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"
DEFAULT_RUNS_ROOT = Path(os.environ.get("WINDLAB_RUNS", "/runs"))

# Air at sea level, International Standard Atmosphere (15 degC).
RHO_SEA_LEVEL = 1.225  # kg/m^3
NU_SEA_LEVEL = 1.4607e-5  # m^2/s
SPEED_OF_SOUND = 340.29  # m/s

# Freestream turbulence per NASA TMR / PRD SIM-5.
FREESTREAM_TU = 0.001  # 0.1 %
FREESTREAM_NUT_RATIO = 0.2

MAX_ITERATIONS_CAP = 3000  # SEC-7
MAX_STL_BYTES = 200 * 1024 * 1024  # GEO-1

Finite = Annotated[float, Field(allow_inf_nan=False)]


class InputError(ValueError):
    """Raised with a plain-language message when inputs are unusable."""


# --------------------------------------------------------------------------- inputs


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FlowConditions(_Strict):
    airspeed: Finite = Field(ge=1.0, le=100.0, description="Freestream speed, m/s (Mach < 0.3)")
    alpha_deg: Finite = Field(ge=-20.0, le=20.0, description="Angle of attack, degrees")


class ReferenceValues(_Strict):
    area: Finite = Field(gt=1e-6, le=1e4, description="Reference area, m^2")
    length: Finite = Field(ge=1e-3, le=100.0, description="Reference chord / length, m")
    moment_point: tuple[Finite, Finite, Finite] = (0.0, 0.0, 0.0)


class GeometryInput(_Strict):
    """A geometry that has already passed the geometry checks (watertight, in metres).

    ``thickness`` and ``surface_area`` (the check report's thickness block in metres and the
    surface area in m²) switch on the gap refinement around thin plates and fins
    (:mod:`windlab.geometry.thin`); built-in shapes leave them out and their meshes never change."""

    stl_path: Path
    bounds_min: tuple[Finite, Finite, Finite]
    bounds_max: tuple[Finite, Finite, Finite]
    thickness: dict | None = None
    surface_area: Finite | None = Field(default=None, gt=0)

    @field_validator("stl_path")
    @classmethod
    def _stl_exists(cls, p: Path) -> Path:
        if not p.is_file():
            raise ValueError(f"geometry file not found: {p}")
        if p.stat().st_size > MAX_STL_BYTES:
            raise ValueError("geometry file is larger than 200 MB")
        return p

    @model_validator(mode="after")
    def _bounds_ok(self):
        ext = np.subtract(self.bounds_max, self.bounds_min)
        if np.any(ext <= 0):
            raise ValueError("geometry bounding box is empty")
        if np.any(ext > 100.0):
            raise ValueError("geometry is larger than 100 m - check the units (mm vs m)")
        if np.all(ext < 1e-3):
            raise ValueError("geometry is smaller than 1 mm - check the units (mm vs m)")
        return self


class Preset(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DETAILED = "detailed"
    CHECK = "check"  # internal: the tiny installation test of `windlab doctor`, never offered in the app


@dataclass(frozen=True)
class MeshPreset:
    """Mesh family member. Only ``bg_cell_over_l`` changes between presets (SIM-4)."""

    bg_cell_over_l: float  # background cell edge / reference length
    max_iterations: int
    max_global_cells: int  # SEC-7 cap passed to snappyHexMesh
    target_cells: int  # nominal size, for display


# Refinement levels and layer settings shared by all presets (one refinement family).
SURFACE_LEVELS = (7, 8)
FEATURE_LEVEL = 8
# Refinement band around thin plates and fins (windlab.geometry.thin): every cell within THIN_BAND_CELLS
# fine cells of the thin triangles (constant/triSurface/thin.stl) is refined one or two levels above
# SURFACE_LEVELS[1], as the feature's thickness demands - within a budget of about thin.CELL_BUDGET added
# cells (task 014), above which the band gets one level less or is left out.
THIN_BAND_CELLS = thin.BAND_CELLS
NEAR_LEVEL, WAKE_LEVEL, FARWAKE_LEVEL = 5, 4, 3
N_LAYERS = 5
LAYER_EXPANSION = 1.2
LAYER_FINAL_THICKNESS = 0.5  # relative to the local surface cell

# Standard and Detailed shrink the background cell by >= 1.3x per step (SIM-4).
# Quick is measured; Standard/Detailed are scaled and NOT yet measured.
PRESETS: dict[Preset, MeshPreset] = {
    Preset.QUICK: MeshPreset(2.0, 1500, 1_500_000, 400_000),
    Preset.STANDARD: MeshPreset(2.0 / 1.55, 2000, 5_000_000, 1_500_000),
    Preset.DETAILED: MeshPreset(2.0 / 2.35, 3000, 15_000_000, 5_000_000),
    Preset.CHECK: MeshPreset(4.0, 40, 400_000, 60_000),
}


# --------------------------------------------------------------------------- outputs


@dataclass(frozen=True)
class Case:
    run_id: str
    path: Path
    params: dict

    def to_json(self) -> str:
        return json.dumps({"run_id": self.run_id, "path": str(self.path), **self.params}, indent=2)


# --------------------------------------------------------------------------- helpers


def _num(x) -> str:
    if isinstance(x, (bool, np.bool_)):
        raise TypeError("booleans are not dictionary values")
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    x = float(x)
    if not math.isfinite(x):
        raise InputError("internal error: non-finite value in case parameters")
    s = f"{x:.10g}"
    return "0" if s == "-0" else s


def _vec(v) -> str:
    return " ".join(_num(c) for c in v)


_PLACEHOLDER = re.compile(r"@@([A-Z0-9_]+)@@")
_FORBIDDEN = re.compile(r"#codeStream|#calc|#eval|\bcoded\w*|systemCall|#include(?!Etc\b)|#includeFunc|\$\(")
_ALLOWED_ETC = {'"caseDicts/setConstraintTypes"', '"caseDicts/meshQualityDict"'}


def render(text: str, values: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"template placeholder @@{key}@@ has no value")
        return values[key]

    out = _PLACEHOLDER.sub(sub, text)
    check_dictionary_safe(out)
    return out


def check_dictionary_safe(text: str) -> None:
    """Reject dictionary text containing code-execution or include directives (SEC-2)."""
    m = _FORBIDDEN.search(text)
    if m:
        raise InputError(f"refusing to write dictionary containing {m.group(0)!r}")
    for inc in re.findall(r"#includeEtc\s+(\S+)", text):
        if inc not in _ALLOWED_ETC:
            raise InputError(f"refusing to write dictionary with #includeEtc {inc}")
    if "@@" in text:
        raise InputError("unfilled template placeholder")


def safe_join(root: Path, *parts: str) -> Path:
    root = root.resolve()
    p = root.joinpath(*parts).resolve()
    if p == root or root not in p.parents:
        raise InputError("path escapes the runs folder")
    return p


def freestream_directions(alpha_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Drag (along the wind) and lift (normal to it, in the x-z plane) unit vectors."""
    a = math.radians(alpha_deg)
    drag = np.array([math.cos(a), 0.0, math.sin(a)])
    lift = np.array([-math.sin(a), 0.0, math.cos(a)])
    return drag, lift


def reynolds_number(airspeed: float, length: float, nu: float = NU_SEA_LEVEL) -> float:
    return airspeed * length / nu


# A velocity component below this fraction of the wind speed is "nearly zero everywhere":
# its normalised residual says nothing about convergence (see ConvergenceMonitor).
GUARD_MIN_COMPONENT = 0.05

# The everyday stop rule (windlab.runner.ConvergenceMonitor): trailing means over the last
# 100 iterations and the 100 before a lag of 50 must agree within 0.25 % (CL) and 0.5 %
# (CD), with every guarded residual below 1e-4. Until task 010 the rule used 200/100 with
# 0.5 % / 1 %; replayed on the recorded Quick histories (tests/data/convergence) the new
# rule stops at 240 / 185 iterations instead of 375 / 320 with CL and CD within 0.01 %
# of the long-run values - the solution is settled by iteration ~200, the old rule simply
# could not fire before 300. The old rule stays as a fallback tried alongside: a slowly
# oscillating case near stall (the wing at 12 deg) satisfies the longer window first, so
# no case takes longer than before. Reported CL/CD are means over the window that fired.
EVERYDAY_CONVERGENCE = {"window": 100, "lag": 50, "cl_tol": 0.0025, "cd_tol": 0.005, "cl_floor": 0.001,
                        "residual_tol": 1e-4,
                        "fallback": {"window": 200, "lag": 100, "cl_tol": 0.005, "cd_tol": 0.01, "cl_floor": 0.002}}


def residual_guard_fields(u_vec, min_fraction: float = GUARD_MIN_COMPONENT) -> list[str]:
    """The solverInfo fields the stop rule's residual guard should look at.

    Pressure and the turbulence fields always; of the velocity components only those
    that carry at least ``min_fraction`` of the wind speed. At zero incidence that is
    ``Ux`` alone: ``Uy`` (spanwise) and ``Uz`` are normalised by nearly-zero fields and
    their residuals hover around the tolerance without ever dropping (the fine 0 deg run
    of the mesh study, 2026-11-03, ran to the cap with CD constant for 2000 iterations).
    """
    u = np.asarray(u_vec, dtype=float)
    speed = float(np.linalg.norm(u))
    fields = ["p", "k", "omega"]
    for name, comp in zip(("Ux", "Uy", "Uz"), u, strict=True):
        if speed > 0 and abs(comp) >= min_fraction * speed:
            fields.append(name)
    return fields


def _box(lo, hi, before, after, side_y, side_z, L) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.array([lo[0] - before * L, lo[1] - side_y * L, lo[2] - side_z * L]),
        np.array([hi[0] + after * L, hi[1] + side_y * L, hi[2] + side_z * L]),
    )


# --------------------------------------------------------------------------- builder


def case_parameters(
    geometry: GeometryInput,
    conditions: FlowConditions,
    reference: ReferenceValues,
    preset: Preset,
    n_procs: int,
    mesh_preset: MeshPreset | None = None,
) -> dict:
    """Compute every number the template needs. Pure function, no I/O.

    ``mesh_preset`` replaces the named preset's numbers (mesh studies in ``scripts/``);
    the app never passes it."""
    preset = Preset(preset)
    mp = mesh_preset or PRESETS[preset]
    L = reference.length
    lo = np.array(geometry.bounds_min, dtype=float)
    hi = np.array(geometry.bounds_max, dtype=float)

    n_procs = int(max(1, min(int(n_procs), os.cpu_count() or 1, 64)))

    # Background mesh: >= 20 L upstream and to the sides, >= 40 L downstream (ENV-4).
    h = mp.bg_cell_over_l * L
    x0 = lo[0] - 20 * L
    y0 = lo[1] - 20 * L
    z0 = lo[2] - 20 * L
    nx = math.ceil((hi[0] + 40 * L - x0) / h)
    ny = math.ceil((hi[1] + 20 * L - y0) / h)
    nz = math.ceil((hi[2] + 20 * L - z0) / h)
    # centre the body laterally/vertically after rounding up
    y0 -= 0.5 * (ny * h - (hi[1] - lo[1] + 40 * L))
    z0 -= 0.5 * (nz * h - (hi[2] - lo[2] + 40 * L))
    x1, y1, z1 = x0 + nx * h, y0 + ny * h, z0 + nz * h
    if nx * ny * nz > 2_000_000:
        raise InputError("background mesh too large - check the reference length")

    # Refinement boxes. Generous in z so the wake stays refined for |alpha| <= 20 deg.
    near = _box(lo, hi, 0.25, 0.5, 0.25, 0.3, L)
    wake = _box(lo, hi, 0.5, 3.0, 0.5, 1.0, L)
    farwake = _box(lo, hi, 1.0, 8.0, 1.0, 2.5, L)

    # A point well inside the fluid, never on a face at any refinement level.
    loc = np.array([x1 - 1.371 * h, y1 - 1.413 * h, z1 - 1.297 * h])

    # Mid-span slice for the flow picture: the plane y = centre of the body box (nudged
    # off the cell faces) across the whole tunnel; the page shows a close-up around the
    # body by default and the whole tunnel on request (windlab.app.flowlines).
    yc = 0.5 * (lo[1] + hi[1]) + 0.0137 * h / 2 ** SURFACE_LEVELS[1]
    slice_min = [x0, y0, z0]
    slice_max = [x1, y1, z1]

    U = conditions.airspeed
    drag, lift = freestream_directions(conditions.alpha_deg)
    k_inf = 1.5 * (FREESTREAM_TU * U) ** 2
    nut_inf = FREESTREAM_NUT_RATIO * NU_SEA_LEVEL
    omega_inf = k_inf / nut_inf

    max_cells = int(mp.max_global_cells)
    surface_cell = h / 2 ** SURFACE_LEVELS[1]
    thin_found = thin.thin_features(geometry.thickness, surface_cell, geometry.surface_area, n_layers=N_LAYERS)
    band = None
    if thin_found and thin_found["extra_levels"] > 0:  # 0: the cell budget leaves the mesh plain (thin.CELL_BUDGET)
        level = int(SURFACE_LEVELS[1] + thin_found["extra_levels"])
        band = {"level": level, "distance": THIN_BAND_CELLS * h / 2 ** level, "triangles": None}
    return {
        "preset": preset.value,
        "mesh_preset": {"bg_cell_over_l": mp.bg_cell_over_l, "target_cells": mp.target_cells},
        "n_procs": n_procs,
        "airspeed": U,
        "alpha_deg": conditions.alpha_deg,
        "reynolds": reynolds_number(U, L),
        "mach": U / SPEED_OF_SOUND,
        "rho": RHO_SEA_LEVEL,
        "nu": NU_SEA_LEVEL,
        "a_ref": reference.area,
        "l_ref": L,
        "cofr": list(reference.moment_point),
        "drag_dir": drag.tolist(),
        "lift_dir": lift.tolist(),
        "u_vec": (U * drag).tolist(),
        "k_inf": k_inf,
        "omega_inf": omega_inf,
        "nut_inf": nut_inf,
        "background_cell": h,
        "background_cells": [nx, ny, nz],
        "domain_min": [x0, y0, z0],
        "domain_max": [x1, y1, z1],
        "boxes": {
            "near": [near[0].tolist(), near[1].tolist(), NEAR_LEVEL],
            "wake": [wake[0].tolist(), wake[1].tolist(), WAKE_LEVEL],
            "farwake": [farwake[0].tolist(), farwake[1].tolist(), FARWAKE_LEVEL],
        },
        "surface_levels": list(SURFACE_LEVELS),
        "surface_cell": surface_cell,
        "feature_level": FEATURE_LEVEL,
        "thin_features": thin_found,  # windlab.geometry.thin: what is thin on this part at this cell size, or None
        # the refinement band around the thin triangles (level, half-width in m, triangle count once written), or None
        "thin_refinement": band,
        "n_layers": N_LAYERS,
        "location_in_mesh": loc.tolist(),
        "slice_point": [0.5 * (lo[0] + hi[0]), float(yc), 0.5 * (lo[2] + hi[2])],
        "slice_bounds": [slice_min, slice_max],
        "body_min": lo.tolist(),
        "body_max": hi.tolist(),
        "max_global_cells": max_cells,
        "max_local_cells": max(100_000, max_cells // n_procs),
        "max_iterations": int(min(mp.max_iterations, MAX_ITERATIONS_CAP)),
        "target_cells": mp.target_cells,
        # everyday stop rule with the residual guard limited to the fields that carry freestream
        "convergence": {**EVERYDAY_CONVERGENCE, "guard_fields": residual_guard_fields(U * drag)},
    }


def _thin_entries(band: dict | None) -> tuple[str, str]:
    """The snappyHexMesh entries for the refinement band around thin parts: the ``thin.stl``
    geometry and the ``distance`` refinement region on it (empty strings when there is none).
    Fixed keywords and clamped numbers only."""
    if not band or not band.get("triangles"):
        return "", ""
    level = int(max(0, min(int(band["level"]), 12)))
    distance = float(band["distance"])
    if not math.isfinite(distance) or distance <= 0:
        raise InputError("internal error: bad thin-feature band")
    return ("thinParts { type triSurfaceMesh; file \"thin.stl\"; }",
            f"thinParts {{ mode distance; levels (({_num(distance)} {level})); }}")


def template_values(p: dict) -> dict[str, str]:
    b = p["boxes"]
    return {
        "X0": _num(p["domain_min"][0]),
        "Y0": _num(p["domain_min"][1]),
        "Z0": _num(p["domain_min"][2]),
        "X1": _num(p["domain_max"][0]),
        "Y1": _num(p["domain_max"][1]),
        "Z1": _num(p["domain_max"][2]),
        "NX": _num(p["background_cells"][0]),
        "NY": _num(p["background_cells"][1]),
        "NZ": _num(p["background_cells"][2]),
        "N_PROCS": _num(p["n_procs"]),
        "NEAR_MIN": _vec(b["near"][0]),
        "NEAR_MAX": _vec(b["near"][1]),
        "NEAR_LEVEL": _num(b["near"][2]),
        "WAKE_MIN": _vec(b["wake"][0]),
        "WAKE_MAX": _vec(b["wake"][1]),
        "WAKE_LEVEL": _num(b["wake"][2]),
        "FARWAKE_MIN": _vec(b["farwake"][0]),
        "FARWAKE_MAX": _vec(b["farwake"][1]),
        "FARWAKE_LEVEL": _num(b["farwake"][2]),
        "SURFACE_LEVEL_MIN": _num(p["surface_levels"][0]),
        "SURFACE_LEVEL_MAX": _num(p["surface_levels"][1]),
        "FEATURE_LEVEL": _num(p["feature_level"]),
        "THIN_GEOMETRY": _thin_entries(p.get("thin_refinement"))[0],
        "THIN_REFINEMENT": _thin_entries(p.get("thin_refinement"))[1],
        "N_LAYERS": _num(p["n_layers"]),
        "LAYER_EXPANSION": _num(LAYER_EXPANSION),
        "LAYER_FINAL_THICKNESS": _num(LAYER_FINAL_THICKNESS),
        "LOCATION_IN_MESH": _vec(p["location_in_mesh"]),
        "SLICE_POINT": _vec(p["slice_point"]),
        "SLICE_MIN": _vec(p["slice_bounds"][0]),
        "SLICE_MAX": _vec(p["slice_bounds"][1]),
        "MAX_GLOBAL_CELLS": _num(p["max_global_cells"]),
        "MAX_LOCAL_CELLS": _num(p["max_local_cells"]),
        "MAX_ITERATIONS": _num(p["max_iterations"]),
        "RHO": _num(p["rho"]),
        "NU": _num(p["nu"]),
        "U_MAG": _num(p["airspeed"]),
        "U_VEC": _vec(p["u_vec"]),
        "DRAG_DIR": _vec(p["drag_dir"]),
        "LIFT_DIR": _vec(p["lift_dir"]),
        "COFR": _vec(p["cofr"]),
        "L_REF": _num(p["l_ref"]),
        "A_REF": _num(p["a_ref"]),
        "K_INF": _num(p["k_inf"]),
        "OMEGA_INF": _num(p["omega_inf"]),
        "NUT_INF": _num(p["nut_inf"]),
        "TU_PERCENT": _num(FREESTREAM_TU * 100),
        "NUT_RATIO": _num(FREESTREAM_NUT_RATIO),
    }


def build_case(
    geometry: GeometryInput,
    conditions: FlowConditions,
    reference: ReferenceValues,
    preset: Preset | str = Preset.QUICK,
    n_procs: int = 4,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    template: str = "external_aero",
    mesh_preset: MeshPreset | None = None,
) -> Case:
    """Write a ready-to-run case to ``<runs_root>/<uuid4>/`` and return it."""
    params = case_parameters(geometry, conditions, reference, Preset(preset), n_procs, mesh_preset)
    thin_tris = None
    if params["thin_refinement"]:
        thin_tris = _thin_surface(geometry, params["thin_features"]["limit"])
        if thin_tris is None:
            params["thin_refinement"] = None
        else:
            params["thin_refinement"]["triangles"] = int(len(thin_tris))
    values = template_values(params)

    if template not in {"external_aero"}:
        raise InputError(f"unknown case template {template!r}")
    src = TEMPLATE_ROOT / template

    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    dest = safe_join(runs_root, run_id)
    dest.mkdir(mode=0o750)

    try:
        for f in sorted(src.rglob("*")):
            if f.is_symlink():
                raise InputError("template contains a symlink")
            rel = f.relative_to(src)
            out = dest / rel
            if f.is_dir():
                out.mkdir(exist_ok=True)
                continue
            if f.name == ".keep":
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render(f.read_text(), values))
        tri = dest / "constant" / "triSurface"
        tri.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(geometry.stl_path, tri / "body.stl", follow_symlinks=False)
        if thin_tris is not None:
            write_binary_stl(tri / "thin.stl", thin_tris.reshape(-1, 3), np.arange(3 * len(thin_tris)).reshape(-1, 3),
                             solid_name="thin parts")
        meta = {"run_id": run_id, "template": template, **params}
        (dest / "windlab_case.json").write_text(json.dumps(meta, indent=2))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    return Case(run_id=run_id, path=dest, params=params)


def _thin_surface(geometry: GeometryInput, limit: float) -> np.ndarray | None:
    """The triangles of the thin parts (N, 3, 3) from the geometry's STL, or None when none qualify."""
    rec, _header = orient.read_records(geometry.stl_path)
    tris = np.stack([rec["v0"], rec["v1"], rec["v2"]], axis=1).astype(np.float64)
    mask = thin.thin_triangles(tris, geometry.thickness, limit)
    return tris[mask] if mask.any() else None


MAX_SWEEP_ANGLES = 8
DEFAULT_SWEEP_ANGLES = (-4.0, 0.0, 4.0, 8.0, 12.0, 16.0)
# The files that change with the angle of attack (freestream direction, force directions,
# the warm-start patch of the far-field values).
SWEEP_FILES = ("0.orig/U", "system/controlDict", "system/changeDictionaryDict")


def sweep_angles(angles) -> list[float]:
    """Validated, sorted list of 2..8 distinct angles in [-20, 20] degrees."""
    try:
        vals = [float(FlowConditions(airspeed=10.0, alpha_deg=a).alpha_deg) for a in angles]
    except (TypeError, ValueError):
        raise InputError("each angle must be a number between -20 and 20 degrees") from None
    vals = sorted({round(v, 3) for v in vals})
    if len(vals) < 2:
        raise InputError("a sweep needs at least two different angles")
    if len(vals) > MAX_SWEEP_ANGLES:
        raise InputError(f"a sweep can have at most {MAX_SWEEP_ANGLES} angles")
    return vals


def build_sweep_case(
    geometry: GeometryInput,
    airspeed: float,
    angles,
    reference: ReferenceValues,
    preset: Preset | str = Preset.QUICK,
    n_procs: int = 4,
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> Case:
    """One mesh, several angles: the case for the first angle plus, for every angle,
    its own rendering of the angle-dependent files in ``sweep/p<i>/`` (see
    :func:`windlab.runner.run_case`). The mesh and its refinement boxes do not depend on
    the angle (the wake boxes are tall enough for |alpha| <= 20 deg)."""
    vals = sweep_angles(angles)
    case = build_case(geometry, FlowConditions(airspeed=airspeed, alpha_deg=vals[0]), reference, preset,
                      n_procs, runs_root)
    src = TEMPLATE_ROOT / "external_aero"
    try:
        points = []
        for i, a in enumerate(vals):
            p = case_parameters(geometry, FlowConditions(airspeed=airspeed, alpha_deg=a), reference,
                                Preset(preset), n_procs)
            values = template_values(p)
            d = safe_join(case.path, "sweep", f"p{i}")
            d.mkdir(parents=True)
            for rel in SWEEP_FILES:
                (d / Path(rel).name).write_text(render((src / rel).read_text(), values))
            points.append({"alpha_deg": a, "u_vec": p["u_vec"], "drag_dir": p["drag_dir"], "lift_dir": p["lift_dir"],
                           "convergence": p["convergence"]})
        meta_f = case.path / "windlab_case.json"
        meta = json.loads(meta_f.read_text())
        meta["sweep"] = {"angles": vals, "points": points}
        meta_f.write_text(json.dumps(meta, indent=2))
    except Exception:
        shutil.rmtree(case.path, ignore_errors=True)
        raise
    params = {**case.params, "alpha_deg": None, "angles": vals}
    return Case(run_id=case.run_id, path=case.path, params=params)


__all__ = [
    "DEFAULT_SWEEP_ANGLES",
    "EVERYDAY_CONVERGENCE",
    "MAX_SWEEP_ANGLES",
    "build_sweep_case",
    "sweep_angles",
    "Case",
    "FlowConditions",
    "GeometryInput",
    "InputError",
    "MeshPreset",
    "PRESETS",
    "Preset",
    "ReferenceValues",
    "build_case",
    "case_parameters",
    "check_dictionary_safe",
    "freestream_directions",
    "residual_guard_fields",
    "reynolds_number",
    "safe_join",
]
