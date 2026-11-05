"""Mirror symmetry of uploaded parts and what WindLab says about lift at 0 degrees (task 012, step 3)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import make_client
from tests.test_app import CUBE_F, CUBE_V, FakeSolver, naca
from windlab import expected_error as ee
from windlab.app import Settings, create_app
from windlab.app.runs import symmetry_notes
from windlab.geometry import _stl_worker as worker
from windlab.geometry.naca import naca_wing
from windlab.geometry.stl import write_binary_stl
from windlab.geometry.upload import check_stl

# --------------------------------------------------------------------------- the measure


def test_cube_is_symmetric_about_all_three_planes(tmp_path):
    p = tmp_path / "cube.stl"
    write_binary_stl(p, CUBE_V * 100.0, CUBE_F)
    r = check_stl(p, "mm")
    assert r.mirror_symmetric == {"x": True, "y": True, "z": True}
    assert r.counts["mirror_match"] == pytest.approx([1.0, 1.0, 1.0])


def test_a_pushed_corner_breaks_the_symmetry(tmp_path):
    v = CUBE_V.copy()
    v[6] += [0.0, 0.0, 0.4]  # the top corner at (1, 1, 1) moves up: no longer symmetric in any plane
    p = tmp_path / "skew.stl"
    write_binary_stl(p, v * 100.0, CUBE_F)
    r = check_stl(p, "mm")
    assert r.mirror_symmetric == {"x": False, "y": False, "z": False}
    assert all(m < 0.95 for m in r.counts["mirror_match"])


def test_symmetric_and_cambered_wings(tmp_path):
    for code, z_sym in (("0012", True), ("2412", False)):
        w = naca_wing(code, chord=0.3, span=0.6)
        p = tmp_path / f"{code}.stl"
        p.unlink(missing_ok=True)
        w.write_stl(p)
        r = check_stl(p, "m")
        assert r.mirror_symmetric["y"] is True, code  # tip to tip
        assert r.mirror_symmetric["z"] is z_sym, (code, r.counts["mirror_match"])
        assert r.mirror_symmetric["x"] is False, code  # a wing has a nose and a tail


def test_mirror_match_tolerates_small_noise():
    rng = np.random.default_rng(1)
    pts = rng.uniform(-1, 1, (500, 3))
    pts = np.vstack([pts, pts * [1, 1, -1]])  # exactly z-symmetric set
    lo, hi = pts.min(0), pts.max(0)
    tol = float(np.linalg.norm(hi - lo)) * 1e-4
    # float32 STL coordinates carry about 1e-7 of the size as noise: a hundredth of the matching tolerance
    noisy = pts + rng.uniform(-0.01, 0.01, pts.shape) * tol
    m = worker._mirror_match(noisy, noisy.min(0), noisy.max(0), tol)
    assert m[2] > 0.9 and m[0] < 0.2 and m[1] < 0.2
    assert worker._mirror_match(np.zeros((0, 3)), lo, hi, tol) == [0.0, 0.0, 0.0]


# --------------------------------------------------------------------------- the note on the run


def test_symmetry_notes_rules():
    params = {"geometry_class": "unknown", "mirror_symmetric": {"x": False, "y": True, "z": True}}
    notes = symmetry_notes(params, {"cl": -0.0137, "cd": 0.0204}, 0.0)
    assert len(notes) == 1 and "mirror-symmetric top to bottom" in notes[0]
    assert "-0.014" in notes[0] and "67 % of the drag" in notes[0] and "not physics" in notes[0]
    assert symmetry_notes(params, {"cl": -0.0137, "cd": 0.0204}, 5.0) == []  # at an angle lift is real
    assert symmetry_notes(params, {"cl": 0.0004, "cd": 0.02}, 0.0) == []  # too small to mention
    assert symmetry_notes({**params, "mirror_symmetric": {"x": False, "y": True, "z": False}},
                          {"cl": 0.05, "cd": 0.02}, 0.0) == []  # not symmetric top to bottom
    assert symmetry_notes({**params, "geometry_class": "streamlined-lifting"}, {"cl": 0.05, "cd": 0.02}, 0.0) == []
    assert symmetry_notes({}, {"cl": 0.05, "cd": 0.02}, 0.0) == []


def test_expected_error_text_mentions_mesh_noise_for_uploads_near_zero():
    r = ee.coefficient_range("cl", 0.018, "unknown", "quick", 5e5, 0.0, part_type="body")
    assert r["status"] == "unvalidated" and "symmetric top to bottom" in r["why"] and "42 cm rocket" in r["why"]
    assert "refines the mesh around such parts" in r["why"]
    assert "treated as a body" in r["reasons"][0]
    assert "symmetric" not in ee.coefficient_range("cd", 0.02, "unknown", "quick", 5e5, 0.0)["why"]
    assert "symmetric" not in ee.coefficient_range("cl", 0.3, "unknown", "quick", 5e5, 8.0)["why"]
    assert "symmetric" not in ee.coefficient_range("cl", 0.3, "streamlined-lifting", "quick", 5e5, 0.0)["why"]


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def test_run_on_a_symmetric_upload_at_zero_carries_the_note(env, tmp_path):
    a, c = env
    p = tmp_path / "cube.stl"
    write_binary_stl(p, CUBE_V * 300.0, CUBE_F)
    g = c.post("/api/geometry", params={"units": "mm", "name": "cube.stl"}, content=p.read_bytes()).json()
    gid = g["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()  # the fake solver always reports CL 0.45
    assert s["params"]["mirror_symmetric"] == {"x": True, "y": True, "z": True}
    note = next(w for w in s["warnings"] if "mirror-symmetric" in w)
    assert "0.450" in note and "not physics" in note
    assert note in c.get(f"/api/runs/{rid}/report.md").text
    # at an angle, or for a wing, no such note
    rid2 = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4}).json()["run_id"]
    rid3 = c.post("/api/runs", json={"geometry_id": naca(c).json()["geometry_id"], "airspeed": 20,
                                     "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    assert not any("mirror-symmetric" in w for w in c.get(f"/api/runs/{rid2}").json()["warnings"])
    assert not any("mirror-symmetric" in w for w in c.get(f"/api/runs/{rid3}").json()["warnings"])
    # a sweep through 0 degrees goes through the same check per point; the fake solver's CL at 0 is exactly
    # zero, below the threshold, so no note there either (the rule itself is covered above)
    rid4 = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep",
                                     "angles": [0, 4]}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    pts = c.get(f"/api/runs/{rid4}").json()["result"]["points"]
    assert pts[0]["cl"] == 0 and not any("mirror-symmetric" in w for w in pts[0]["warnings"])
    assert not any("mirror-symmetric" in w for w in pts[1]["warnings"])


def test_turning_the_part_turns_its_mirror_planes(env, tmp_path):
    _, c = env
    v = CUBE_V * [300.0, 200.0, 100.0]
    v[6] += [0.0, 0.0, 40.0]  # breaks the z symmetry (and x and y: one corner only)
    v[7] += [0.0, 0.0, 40.0]  # the whole top edge at y = 200 rises: y asymmetric, z asymmetric, x symmetric
    p = tmp_path / "wedge.stl"
    write_binary_stl(p, v, CUBE_F)
    g = c.post("/api/geometry", params={"units": "mm", "name": "wedge.stl"}, content=p.read_bytes()).json()
    gid = g["geometry_id"]
    r = c.get(f"/api/geometry/{gid}").json()
    assert r["usable"]
    meta = c.get(f"/api/geometry/{gid}").json()
    assert meta  # the view does not expose the flags; the run parameters do
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    sym = c.get(f"/api/runs/{rid}").json()["params"]["mirror_symmetric"]
    assert sym == {"x": True, "y": False, "z": False}
    c.post(f"/api/geometry/{gid}/orient", json={"rotate": "z"})  # x -> y: the x mirror plane is now the y plane
    rid2 = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert c.get(f"/api/runs/{rid2}").json()["params"]["mirror_symmetric"] == {"x": False, "y": True, "z": False}
