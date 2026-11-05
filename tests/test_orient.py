"""Part orientation (quarter turns, suggestion) and reference-value overrides."""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import make_client
from tests.test_app import CUBE_F, CUBE_V, FakeSolver, naca
from windlab.app import Settings, create_app
from windlab.geometry import orient
from windlab.geometry.stl import write_binary_stl


@pytest.fixture
def app(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    yield a
    a.fastapi.state.runs.stop()


@pytest.fixture
def client(app):
    return make_client(app)


def box_stl(tmp_path, size_mm):
    """A closed box of the given size (mm), corner at the origin."""
    p = tmp_path / "box.stl"
    write_binary_stl(p, CUBE_V * np.asarray(size_mm, float), CUBE_F)
    return p.read_bytes()


def upload(client, tmp_path, size_mm=(50, 400, 20)):
    r = client.post("/api/geometry", params={"units": "mm", "name": "plate.stl"}, content=box_stl(tmp_path, size_mm))
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- pure functions


@pytest.mark.parametrize("axis, v, expected", [
    ("z", (1, 0, 0), (0, 1, 0)), ("z", (0, 1, 0), (-1, 0, 0)),
    ("x", (0, 1, 0), (0, 0, 1)), ("y", (0, 0, 1), (1, 0, 0)),
])
def test_quarter_turn_is_right_handed(axis, v, expected):
    assert tuple(orient.quarter_turn(axis) @ np.array(v)) == expected


def test_four_quarter_turns_are_identity_and_all_are_rotations():
    for a in "xyz":
        assert np.array_equal(orient.quarter_turn(a, 4), np.eye(3))
        assert orient.is_quarter_turn_rotation(orient.quarter_turn(a))
    assert not orient.is_quarter_turn_rotation(np.diag([1, 1, -1]))  # a mirror image is not allowed
    assert not orient.is_quarter_turn_rotation(np.eye(3) * 2)


@pytest.mark.parametrize("size", [(1, 2, 3), (3, 1, 2), (2, 3, 1), (0.1, 5, 0.3), (4, 4, 1), (1, 1, 1)])
def test_suggestion_puts_longest_along_x_and_shortest_up(size):
    r = orient.suggest_rotation(size)
    assert orient.is_quarter_turn_rotation(r)
    new = np.abs(r) @ np.asarray(size, float)
    assert new[0] == max(size) and new[2] == min(size)


def test_suggestion_keeps_an_already_good_part():
    assert np.array_equal(orient.suggest_rotation((3, 2, 1)), np.eye(3))


def test_wing_hint():
    assert orient.looks_like_a_wing((0.6, 0.15, 0.02))
    assert not orient.looks_like_a_wing((0.6, 0.5, 0.4))


def test_rotate_stl_round_trip(tmp_path):
    p = tmp_path / "b.stl"
    write_binary_stl(p, CUBE_V * [1, 2, 3], CUBE_F)
    before, _ = orient.read_records(p)
    lo, hi = orient.rotate_stl(p, orient.quarter_turn("z"))
    assert np.allclose(np.subtract(hi, lo), (2, 1, 3))
    for _ in range(3):
        orient.rotate_stl(p, orient.quarter_turn("z"))
    after, _ = orient.read_records(p)
    for key in ("normal", "v0", "v1", "v2"):  # exact: four quarter turns give identical numbers
        assert np.array_equal(after[key], before[key])
    with pytest.raises(ValueError):
        orient.rotate_stl(p, np.diag([1, 1, -1]))


# --------------------------------------------------------------------------- API


def test_orient_upload_updates_size_reference_and_file(client, app, tmp_path):
    g = upload(client, tmp_path)  # 50 x 400 x 20 mm block: a body (not thin enough for a wing), frontal area
    assert g["size_m"] == pytest.approx([0.05, 0.4, 0.02])
    assert g["reference"]["length"] == pytest.approx(0.05) and g["reference"]["area"] == pytest.approx(0.008, rel=1e-2)
    r = client.post(f"/api/geometry/{g['geometry_id']}/orient", json={"rotate": "z"})
    assert r.status_code == 200, r.text
    g2 = r.json()
    assert g2["size_m"] == pytest.approx([0.4, 0.05, 0.02]) and "about the z axis" in g2["note"]
    assert g2["reference"]["length"] == pytest.approx(0.4) and g2["reference"]["area"] == pytest.approx(0.001, rel=2e-2)
    assert any("40 cm x 5 cm x 2 cm" in m for m in g2["messages"]), g2["messages"]
    assert g2["orientation"] == orient.quarter_turn("z").tolist()
    # the stored STL really turned, and the preview follows
    stl = app.fastapi.state.geometry.stl_path(g["geometry_id"])
    rec, _ = orient.read_records(stl)
    pts = np.concatenate([rec["v0"], rec["v1"], rec["v2"]])
    assert np.ptp(pts, axis=0) == pytest.approx([0.4, 0.05, 0.02], rel=1e-6)
    # a run built now uses the new bounds and reference
    rid = client.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0}).json()
    assert rid["params"]["reference_length"] == pytest.approx(0.4)


def test_suggest_orientation_and_wing_hint(client, tmp_path):
    g = upload(client, tmp_path, (20, 100, 400))  # a flat plate standing on its end
    g2 = client.post(f"/api/geometry/{g['geometry_id']}/orient", json={"rotate": "suggest"}).json()
    assert g2["size_m"] == pytest.approx([0.4, 0.1, 0.02])
    assert "longest side is along the wind" in g2["note"] and "span" in g2["note"]
    g3 = client.post(f"/api/geometry/{g['geometry_id']}/orient", json={"rotate": "suggest"}).json()
    assert "already" in g3["note"] and g3["size_m"] == pytest.approx(g2["size_m"])


def test_orient_rejects_bad_requests(client, tmp_path):
    w = naca(client).json()
    r = client.post(f"/api/geometry/{w['geometry_id']}/orient", json={"rotate": "z"})
    assert r.status_code == 400 and "already lined up" in r.json()["detail"]
    g = upload(client, tmp_path)
    for body in ({"rotate": "w"}, {"rotate": "x", "angle": 45}, {"matrix": [[1, 0, 0]]}, {}):
        assert client.post(f"/api/geometry/{g['geometry_id']}/orient", json=body).status_code == 422
    assert client.post("/api/geometry/not-an-id/orient", json={"rotate": "x"}).status_code == 400


def test_reference_override_and_reset(client, tmp_path):
    g = upload(client, tmp_path)
    gid = g["geometry_id"]
    r = client.post(f"/api/geometry/{gid}/reference", json={"area": 0.05})
    assert r.status_code == 200, r.text
    g2 = r.json()
    assert g2["reference"]["area"] == 0.05 and g2["reference"]["length"] == pytest.approx(0.05)
    assert g2["reference"]["how"].startswith("area set by you")
    assert g2["reference_default"]["area"] == pytest.approx(0.008, rel=1e-2)  # frontal area of the block
    run = client.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()
    assert run["params"]["reference_area"] == 0.05
    g3 = client.post(f"/api/geometry/{gid}/reference", json={"length": None, "area": None}).json()
    assert g3["reference"]["area"] == pytest.approx(0.008, rel=1e-2) and g3["reference"]["custom"] == []
    # turning the part resets the student's values (they belonged to the old orientation)
    client.post(f"/api/geometry/{gid}/reference", json={"length": 0.3})
    g4 = client.post(f"/api/geometry/{gid}/orient", json={"rotate": "z"}).json()
    assert g4["reference"]["length"] == pytest.approx(0.4) and not g4["reference"].get("custom")


@pytest.mark.parametrize("body, word", [
    ({"length": 0}, "reference length must be between"),
    ({"length": 500}, "reference length must be between"),
    ({"area": -1}, "reference area must be between"),
    ({"area": "big"}, "area"),
    ({"area": 0.1, "how": "#codeStream"}, "how"),
])
def test_reference_override_rejects_bad_values(client, tmp_path, body, word):
    g = upload(client, tmp_path)
    r = client.post(f"/api/geometry/{g['geometry_id']}/reference", json=body)
    assert r.status_code in (400, 422) and word in r.json()["detail"]


def test_naca_reference_override(client):
    w = naca(client).json()
    g = client.post(f"/api/geometry/{w['geometry_id']}/reference", json={"length": 0.25}).json()
    assert g["reference"]["length"] == 0.25 and g["reference"]["area"] == pytest.approx(0.18)
