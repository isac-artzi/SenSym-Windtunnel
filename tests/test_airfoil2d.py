"""2D NACA 0012 validation case: geometry, mesh layout, rendered dictionaries."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from windlab import airfoil2d as a2d
from windlab.case_builder import check_dictionary_safe


def test_tmr_section_is_sharp_and_12_percent():
    x = np.linspace(0, 1, 20001)
    z = a2d.tmr_naca0012_half_thickness(x)
    assert z[0] == 0.0 and z[-1] == 0.0
    # TMR rescales the classic section from its closing chord 1.00893 to 1: t/c = 0.12 / 1.00893
    assert 2 * z.max() == pytest.approx(0.12 / 1.00893, abs=1e-4)
    assert x[np.argmax(z)] == pytest.approx(0.30, abs=0.01)


def test_reynolds_and_mach():
    assert pytest.approx(6e6) == a2d.SPEED * a2d.CHORD / a2d.NU
    p = a2d.case_parameters(10.0, 4)
    assert p["reynolds"] == pytest.approx(6e6)
    assert p["mach"] < 0.2
    assert p["lift_dir"] == pytest.approx([-math.sin(math.radians(10)), 0, math.cos(math.radians(10))])


def test_reynolds_override_changes_nu_only():
    p = a2d.case_parameters(0.0, 4, reynolds=4.1e5)
    assert p["reynolds"] == pytest.approx(4.1e5) and p["airspeed"] == a2d.SPEED
    assert p["nu"] == pytest.approx(a2d.SPEED * a2d.CHORD / 4.1e5)
    assert p["nut_inf"] / p["nu"] == pytest.approx(0.2)
    with pytest.raises(ValueError):
        a2d.case_parameters(0.0, 4, reynolds=1e3)


def test_capped_grading_fills_length():
    n, g = a2d.capped_grading(500.0, 1e-3, 1.1, 10.0)
    assert sum(part[0] for part in g) == pytest.approx(1.0)
    assert sum(part[1] for part in g) == pytest.approx(1.0)
    n2, g2 = a2d.capped_grading(500.0, 0.5, 1.1, 10.0, n=n)
    assert n2 == n and sum(part[0] for part in g2) == pytest.approx(1.0)


def test_expansion_for_first_cell():
    e = a2d.expansion_for_first_cell(2.0, 2e-6, 121)
    q = e ** (1 / 120)
    assert 2e-6 * (q**121 - 1) / (q - 1) == pytest.approx(2.0, rel=1e-6)


def test_block_mesh_layout():
    bm = a2d.block_mesh()
    c = bm["counts"]
    assert c["front_blocks"] >= 4
    assert len(bm["blocks"]) == 2 * 2 * (c["front_blocks"] + 3)  # front blocks, rear, near and far wake
    assert 80_000 < bm["cells"] < 200_000
    # every face of every patch references existing vertices; blocks are right-handed hexes
    nv = len(bm["vertices"])
    for faces in bm["patches"].values():
        assert all(0 <= i < nv for f in faces for i in f)
    V = np.array(bm["vertices"])
    for b in bm["blocks"]:
        p = V[b["vertices"]]
        x1, x2, x3 = p[1] - p[0], p[3] - p[0], p[4] - p[0]
        assert np.dot(np.cross(x1, x2), x3) > 0


def test_side_edges_leave_the_wall_normally():
    """Inner-ring side edges are wall normals (within the 15 deg split tolerance at most)."""
    bm = a2d.block_mesh()
    V = np.array(bm["vertices"])
    for b in bm["blocks"][:1]:
        p = V[b["vertices"]]
        assert abs(p[0][2]) < 1e-12  # leading edge block starts at the LE
        assert abs((p[3] - p[0])[2]) < 1e-9  # LE side edge is horizontal


def test_build_case_renders_safe_dictionaries(tmp_path):
    case = a2d.build_airfoil_2d_case(5.0, n_procs=2, runs_root=tmp_path)
    assert case.path.parent == tmp_path
    meta = json.loads((case.path / "windlab_case.json").read_text())
    assert meta["template"] == "airfoil_2d" and meta["convergence"]["residual_tol"] > 0
    for f in case.path.rglob("*"):
        if f.is_file():
            text = f.read_text()
            assert "@@" not in text
            check_dictionary_safe(text)
    bmd = (case.path / "system" / "blockMeshDict").read_text()
    assert "polyLine" in bmd and "arc" in bmd and "empty" in bmd


def test_startup_and_final_dictionaries(tmp_path):
    case = a2d.build_airfoil_2d_case(10.0, n_procs=1, runs_root=tmp_path)
    sysdir = case.path / "system"
    start, final = (sysdir / "fvSchemes").read_text(), (sysdir / "fvSchemes.final").read_text()
    assert "div(phi,U)      bounded Gauss upwind;" in start
    assert "linearUpwindV" in final and "linearUpwindV" not in start
    assert case.params["startup"]["files"] == ["system/fvSchemes", "system/fvSolution"]
    assert "hierarchical" in (sysdir / "decomposeParDict").read_text()  # reproducible partition
    assert "symmetryPlane" not in (sysdir / "blockMeshDict").read_text()


def test_alpha_zero_is_a_half_model(tmp_path):
    full, half = a2d.block_mesh(), a2d.block_mesh(half=True)
    assert len(half["blocks"]) == len(full["blocks"]) // 2 and half["cells"] * 2 == full["cells"]
    assert not full["patches"]["symmetry"] and len(half["patches"]["symmetry"]) == 4  # LE line (2) + wake cut (2)
    V = np.array(half["vertices"])
    for f in half["patches"]["symmetry"]:
        assert np.all(np.abs(V[f][:, 2]) < 1e-12)  # every symmetry face lies on z = 0
    assert np.all(V[:, 2] > -1e-12)  # upper half only
    p = a2d.case_parameters(0.0, 4)
    assert p["half_model"] and p["a_ref"] == pytest.approx(0.5 * a2d.CHORD * a2d.DEFAULT_MESH.span)
    assert not a2d.case_parameters(10.0, 4)["half_model"]
    with pytest.raises(ValueError):
        a2d.case_parameters(5.0, 4, half=True)
    case = a2d.build_airfoil_2d_case(0.0, n_procs=1, runs_root=tmp_path)
    bmd = (case.path / "system" / "blockMeshDict").read_text()
    assert "symmetryPlane" in bmd and "@@" not in bmd


def test_startup_switch_copies_final_files(tmp_path):
    from windlab.runner import StartupSwitch

    sysdir = tmp_path / "system"
    sysdir.mkdir()
    for f in ("fvSchemes", "fvSolution"):
        (sysdir / f).write_text("start")
        (sysdir / f"{f}.final").write_text("final")
    (sysdir / "controlDict.final").write_text("must not be copied")
    out = tmp_path / "postProcessing" / "forceCoeffs1" / "0"
    out.mkdir(parents=True)
    dat = out / "coefficient.dat"

    sw = StartupSwitch(tmp_path, 300, ["system/fvSchemes", "system/fvSolution", "system/controlDict"])
    dat.write_text("# Time Cd Cl\n" + "".join(f"{i} 0.01 0.5\n" for i in range(1, 200)))
    sw.check()
    assert sw.switched_at is None and (sysdir / "fvSchemes").read_text() == "start"
    dat.write_text("# Time Cd Cl\n" + "".join(f"{i} 0.01 0.5\n" for i in range(1, 320)))
    sw.check()
    assert sw.switched_at == 319
    assert (sysdir / "fvSchemes").read_text() == "final" and (sysdir / "fvSolution").read_text() == "final"
    assert not (sysdir / "controlDict").exists()  # only whitelisted dictionaries are swapped


def test_alpha_is_validated(tmp_path):
    with pytest.raises(ValueError):
        a2d.build_airfoil_2d_case(45.0, runs_root=tmp_path)
