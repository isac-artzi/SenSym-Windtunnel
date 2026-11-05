import json
import math

import numpy as np
import pytest
from pydantic import ValidationError

from windlab import case_builder as cb
from windlab.case_builder import (
    FlowConditions,
    GeometryInput,
    InputError,
    Preset,
    ReferenceValues,
    build_case,
    case_parameters,
    check_dictionary_safe,
    freestream_directions,
    render,
    safe_join,
)
from windlab.geometry.naca import naca_wing


@pytest.fixture
def wing_geometry(tmp_path):
    w = naca_wing("0012", chord=0.3, span=0.6)
    stl = tmp_path / "wing.stl"
    w.write_stl(stl)
    lo, hi = w.bounds
    geom = GeometryInput(stl_path=stl, bounds_min=tuple(lo), bounds_max=tuple(hi))
    ref = ReferenceValues(area=w.reference_area, length=w.mean_aero_chord, moment_point=w.quarter_chord_point)
    return geom, ref


def _flow(alpha=0.0, speed=20.0):
    return FlowConditions(airspeed=speed, alpha_deg=alpha)


# ------------------------------------------------------------------ input validation


@pytest.mark.parametrize(
    "kwargs",
    [
        {"airspeed": 0.0, "alpha_deg": 0},
        {"airspeed": 150.0, "alpha_deg": 0},  # Mach > 0.3
        {"airspeed": 20.0, "alpha_deg": 45},
        {"airspeed": float("nan"), "alpha_deg": 0},
        {"airspeed": float("inf"), "alpha_deg": 0},
        {"airspeed": "20; #codeStream", "alpha_deg": 0},
        {"airspeed": 20.0, "alpha_deg": 0, "extra": "x"},
    ],
)
def test_flow_conditions_rejects_bad_input(kwargs):
    with pytest.raises(ValidationError):
        FlowConditions(**kwargs)


def test_reference_values_rejects_bad_input():
    with pytest.raises(ValidationError):
        ReferenceValues(area=-1, length=0.3)
    with pytest.raises(ValidationError):
        ReferenceValues(area=0.18, length=0.3, moment_point=(0, 0, float("nan")))


def test_geometry_units_check(tmp_path):
    stl = tmp_path / "x.stl"
    stl.write_bytes(b"\0" * 84)
    with pytest.raises(ValidationError, match="units"):  # a 300 mm part read as 300 m
        GeometryInput(stl_path=stl, bounds_min=(0, -300, -18), bounds_max=(300, 300, 18))
    with pytest.raises(ValidationError):
        GeometryInput(stl_path=tmp_path / "missing.stl", bounds_min=(0, 0, 0), bounds_max=(1, 1, 1))


# ------------------------------------------------------------------ physics set-up


@pytest.mark.parametrize("alpha", [-12.0, 0.0, 5.0, 10.0, 20.0])
def test_freestream_directions_are_orthonormal(alpha):
    drag, lift = freestream_directions(alpha)
    assert np.linalg.norm(drag) == pytest.approx(1)
    assert np.linalg.norm(lift) == pytest.approx(1)
    assert np.dot(drag, lift) == pytest.approx(0, abs=1e-12)
    assert drag[1] == lift[1] == 0
    assert math.degrees(math.atan2(drag[2], drag[0])) == pytest.approx(alpha)
    assert lift[2] > 0  # lift points "up" for any small angle


def test_parameters_naca(wing_geometry):
    geom, ref = wing_geometry
    p = case_parameters(geom, _flow(10.0), ref, Preset.QUICK, n_procs=4)
    assert p["reynolds"] == pytest.approx(20 * 0.3 / cb.NU_SEA_LEVEL)
    assert p["u_vec"] == pytest.approx([20 * math.cos(math.radians(10)), 0, 20 * math.sin(math.radians(10))])
    L = 0.3
    lo, hi = np.array(p["domain_min"]), np.array(p["domain_max"])
    assert geom.bounds_min[0] - lo[0] >= 20 * L - 1e-9  # ENV-4 upstream
    assert hi[0] - geom.bounds_max[0] >= 40 * L - 1e-9  # downstream
    assert geom.bounds_min[1] - lo[1] >= 20 * L - 1e-9 and hi[1] - geom.bounds_max[1] >= 20 * L - 1e-9
    assert geom.bounds_min[2] - lo[2] >= 20 * L - 1e-9 and hi[2] - geom.bounds_max[2] >= 20 * L - 1e-9
    # cubic background cells
    assert (hi - lo) / np.array(p["background_cells"]) == pytest.approx([p["background_cell"]] * 3)
    loc = np.array(p["location_in_mesh"])
    assert np.all(loc > lo) and np.all(loc < hi)
    assert not np.all((loc > geom.bounds_min) & (loc < geom.bounds_max))
    # freestream turbulence: Tu 0.1 %, nut/nu 0.2
    assert math.sqrt(2 / 3 * p["k_inf"]) / 20 == pytest.approx(0.001)
    assert p["k_inf"] / p["omega_inf"] / cb.NU_SEA_LEVEL == pytest.approx(0.2)


def test_presets_form_a_refinement_family(wing_geometry):
    geom, ref = wing_geometry
    sizes = [case_parameters(geom, _flow(), ref, pr, 4)["background_cell"] for pr in Preset]
    assert sizes[0] / sizes[1] >= 1.3 and sizes[1] / sizes[2] >= 1.3  # SIM-4


def test_mesh_preset_override_for_studies(wing_geometry):
    geom, ref = wing_geometry
    base = case_parameters(geom, _flow(), ref, Preset.QUICK, 4)
    fine_preset = cb.MeshPreset(1.0, 3000, 9_000_000, 3_000_000)
    fine = case_parameters(geom, _flow(), ref, Preset.QUICK, 4, mesh_preset=fine_preset)
    assert fine["background_cell"] == pytest.approx(base["background_cell"] / 2)
    assert fine["max_iterations"] == 3000 and fine["max_global_cells"] == 9_000_000
    assert fine["preset"] == "quick" and fine["mesh_preset"]["bg_cell_over_l"] == 1.0
    assert base["mesh_preset"] == {"bg_cell_over_l": 2.0, "target_cells": 400_000}


def test_derived_values_are_clamped(wing_geometry):
    geom, ref = wing_geometry
    p = case_parameters(geom, _flow(), ref, Preset.QUICK, n_procs=10_000)
    assert p["n_procs"] <= 64
    assert case_parameters(geom, _flow(), ref, Preset.QUICK, n_procs=-3)["n_procs"] == 1
    for pr in Preset:
        assert case_parameters(geom, _flow(), ref, pr, 4)["max_iterations"] <= 3000


# ------------------------------------------------------------------ rendering / safety


def test_render_requires_every_placeholder():
    assert render("a @@X@@ b", {"X": "1"}) == "a 1 b"
    with pytest.raises(KeyError):
        render("a @@X@@ @@Y@@", {"X": "1"})


@pytest.mark.parametrize(
    "text",
    [
        "x #codeStream { code #{ system(\"rm -rf ~\"); #}; };",
        "#calc \"1+1\"",
        "type codedFixedValue;",
        "type systemCall;",
        '#include "/etc/passwd"',
        '#includeEtc "../../../etc/passwd"',
        "#includeFunc streamlines",
    ],
)
def test_dictionary_safety_rejects_directives(text):
    with pytest.raises(InputError):
        check_dictionary_safe(text)


def test_safe_join(tmp_path):
    assert safe_join(tmp_path, "abc") == (tmp_path / "abc").resolve()
    for bad in ("..", "../x", "/etc", "a/../../b", ""):
        with pytest.raises(InputError):
            safe_join(tmp_path, bad)


# ------------------------------------------------------------------ full build


def test_build_case_writes_complete_case(wing_geometry, tmp_path):
    geom, ref = wing_geometry
    runs = tmp_path / "runs"
    case = build_case(geom, _flow(10.0), ref, Preset.QUICK, n_procs=4, runs_root=runs)
    import uuid

    assert uuid.UUID(case.run_id).version == 4
    assert case.path.parent == runs.resolve()
    for rel in [
        "system/controlDict", "system/blockMeshDict", "system/snappyHexMeshDict", "system/fvSchemes",
        "system/fvSolution", "system/decomposeParDict", "system/surfaceFeatureExtractDict",
        "constant/transportProperties", "constant/turbulenceProperties", "constant/triSurface/body.stl",
        "0.orig/U", "0.orig/p", "0.orig/k", "0.orig/omega", "0.orig/nut", "windlab_case.json",
    ]:
        assert (case.path / rel).is_file(), rel
    for f in case.path.rglob("*"):
        if f.is_file() and f.suffix != ".stl":
            text = f.read_text()
            assert "@@" not in text, f
            if f.suffix != ".json":
                check_dictionary_safe(text)
    ctrl = (case.path / "system/controlDict").read_text()
    assert "magUInf         20;" in ctrl
    assert "Aref            0.18;" in ctrl
    assert "liftDir         (-0.1736481777 0 0.984807753);" in ctrl
    assert "numberOfSubdomains  4;" in (case.path / "system/decomposeParDict").read_text()
    meta = json.loads((case.path / "windlab_case.json").read_text())
    assert meta["run_id"] == case.run_id and meta["alpha_deg"] == 10.0


def test_two_builds_get_distinct_ids(wing_geometry, tmp_path):
    geom, ref = wing_geometry
    a = build_case(geom, _flow(), ref, runs_root=tmp_path)
    b = build_case(geom, _flow(), ref, runs_root=tmp_path)
    assert a.run_id != b.run_id and a.path != b.path


def test_template_has_only_known_placeholders(wing_geometry):
    geom, ref = wing_geometry
    values = cb.template_values(case_parameters(geom, _flow(), ref, Preset.QUICK, 4))
    import re

    used = set()
    for f in (cb.TEMPLATE_ROOT / "external_aero").rglob("*"):
        if f.is_file():
            used |= set(re.findall(r"@@([A-Z0-9_]+)@@", f.read_text()))
    assert used == set(values)
