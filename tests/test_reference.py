"""Reference area of uploaded parts (task 012, step 2): frontal area for bodies, planform area for wings,
measured from the surface; the wing / body guess and the student's choice through the API."""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import make_client
from tests.test_app import CUBE_F, CUBE_V, FakeSolver, naca
from windlab.app import Settings, create_app
from windlab.geometry import reference, shapes
from windlab.geometry.naca import naca_wing
from windlab.geometry.stl import write_binary_stl

# --------------------------------------------------------------------------- the silhouette


def test_cube_and_rotated_cube_frontal_area():
    tris = CUBE_V[CUBE_F] * 0.3
    assert reference.silhouette_area(tris, 0) == pytest.approx(0.09, rel=1e-3)
    assert reference.silhouette_area(tris, 2) == pytest.approx(0.09, rel=1e-3)
    c, s = np.cos(np.pi / 4), np.sin(np.pi / 4)
    rot = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])  # 45 degrees about the wind axis: a diamond, same area
    assert reference.silhouette_area(tris @ rot.T, 0) == pytest.approx(0.09, rel=3e-3)
    # seen along y the rotated cube is a rectangle 0.3 x 0.3*sqrt(2)
    assert reference.silhouette_area(tris @ rot.T, 1) == pytest.approx(0.09 * np.sqrt(2), rel=3e-3)


def test_sphere_cylinder_and_wing_against_formulas():
    sphere = shapes.make_body("sphere", diameter=0.2)
    assert reference.silhouette_area(sphere.vertices[sphere.faces], 0) == pytest.approx(np.pi * 0.01, rel=2e-3)
    cyl = shapes.make_body("cylinder", diameter=0.15, length=0.45)  # across the wind: a rectangle either way
    t = cyl.vertices[cyl.faces]
    assert reference.silhouette_area(t, 0) == pytest.approx(0.0675, rel=2e-3)
    assert reference.silhouette_area(t, 2) == pytest.approx(0.0675, rel=2e-3)
    wing = naca_wing("0012", chord=0.3, span=0.6)
    t = wing.vertices[wing.faces]
    assert reference.silhouette_area(t, 2) == pytest.approx(0.18, rel=1e-3)  # planform = chord x span
    assert reference.silhouette_area(t, 0) == pytest.approx(0.036 * 0.6, rel=0.02)  # frontal = thickness x span


def test_shadow_counts_overlaps_once_and_beats_the_box():
    # two boxes one behind the other along the wind: the frontal silhouette is one box, not two
    a = CUBE_V[CUBE_F] * 0.1
    b = a + [0.3, 0.0, 0.0]
    assert reference.silhouette_area(np.vstack([a, b]), 0) == pytest.approx(0.01, rel=1e-3)
    # an L seen from the front is less than its bounding box
    c = a + [0.0, 0.1, 0.0]  # beside the first, same height
    d = a + [0.0, 0.0, 0.1]  # on top of the first
    L = np.vstack([a, c, d])
    frontal = reference.silhouette_area(L, 0)
    assert frontal == pytest.approx(0.03, rel=1e-3) and frontal < 0.04  # box would be 0.2 x 0.2


def test_degenerate_input_gives_zero():
    assert reference.silhouette_area(np.zeros((0, 3, 3)), 0) == 0.0
    flat = np.array([[[0, 0, 0], [1, 0, 0], [0.5, 0, 0]]], float)  # a line: no shadow along x
    assert reference.silhouette_area(flat, 0) == 0.0


def test_part_kind_rules():
    assert reference.part_kind((0.3, 0.6, 0.036)) == "wing"  # thin: a wing
    assert reference.part_kind((0.42, 0.12, 0.12)) == "body"  # rocket
    assert reference.part_kind((0.4, 0.18, 0.165)) == "body"  # car
    assert reference.part_kind((0.05, 0.4, 0.02)) == "body"  # a 50 x 400 x 20 mm block: not thin enough
    # wide across the wind and flat (planform >> frontal): a wing with a winglet
    assert reference.part_kind((0.15, 0.42, 0.075), frontal=0.0077, planform=0.061) == "wing"
    assert reference.part_kind((0.15, 0.42, 0.075), frontal=0.03, planform=0.061) == "body"
    assert reference.resolution_for(10) == 1024 and reference.resolution_for(10**6) == 512


def test_measure_and_reference_values(tmp_path):
    p = tmp_path / "rocketish.stl"
    body = shapes.make_body("cylinder", diameter=0.04, length=0.4)  # across the wind in the generator ...
    v = body.vertices[:, [1, 0, 2]]  # ... so swap x and y: a tube 0.4 long along the wind
    write_binary_stl(p, v, body.faces[:, ::-1])
    lo, hi = v.min(0), v.max(0)
    m = reference.measure(lo, hi, p, len(body.faces))
    assert m["method"] == "silhouette" and m["detected"] == "body"
    assert m["frontal"] == pytest.approx(np.pi * 0.02**2, rel=5e-3) and m["planform"] == pytest.approx(0.016, rel=5e-3)
    ref = reference.reference_values(lo, hi, m)
    assert ref["part_type"] == "body" and ref["area"] == m["frontal"] and ref["length"] == pytest.approx(0.4)
    assert "frontal area" in ref["how"] and "measured from the surface" in ref["how"]
    assert ref["moment_point"] == pytest.approx([lo[0] + 0.1, 0.5 * (lo[1] + hi[1]), 0.5 * (lo[2] + hi[2])], abs=1e-9)
    wing = reference.reference_values(lo, hi, m, "wing")
    assert wing["part_type"] == "wing" and wing["area"] == m["planform"] and "planform area" in wing["how"]
    # too many triangles for the rasteriser, or no file: the box, and the text says so
    box = reference.measure(lo, hi, p, reference.MAX_SILHOUETTE_TRIANGLES + 1)
    assert box["method"] == "box" and box["frontal"] == pytest.approx(0.04 * 0.04)
    assert "box around the part" in reference.reference_values(lo, hi, box)["how"]
    assert reference.measure(lo, hi, None)["method"] == "box"
    assert reference.measure(lo, hi, tmp_path / "missing.stl")["method"] == "box"


# --------------------------------------------------------------------------- through the API


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def _upload_box(c, tmp_path, size_mm, name="part.stl"):
    p = tmp_path / name
    write_binary_stl(p, CUBE_V * np.asarray(size_mm, float), CUBE_F)
    r = c.post("/api/geometry", params={"units": "mm", "name": name}, content=p.read_bytes())
    assert r.status_code == 200, r.text
    return r.json()


def test_thin_plate_is_a_wing_and_can_be_made_a_body(env, tmp_path):
    a, c = env
    g = _upload_box(c, tmp_path, (300, 200, 5))  # 300 x 200 x 5 mm: thin, a wing-like plate
    assert g["part_type"] == {"detected": "wing", "chosen": None, "used": "wing"}
    assert g["areas"]["method"] == "silhouette"
    assert g["areas"]["planform"] == pytest.approx(0.06, rel=1e-2)
    assert g["areas"]["frontal"] == pytest.approx(0.001, rel=2e-2)
    assert g["reference"]["area"] == pytest.approx(0.06, rel=1e-2) and "planform area" in g["reference"]["how"]
    gid = g["geometry_id"]
    r = c.post(f"/api/geometry/{gid}/reference", json={"part_type": "body"})
    assert r.status_code == 200, r.text
    g2 = r.json()
    assert g2["part_type"] == {"detected": "wing", "chosen": "body", "used": "body"}
    assert g2["reference"]["area"] == pytest.approx(0.001, rel=2e-2) and "frontal area" in g2["reference"]["how"]
    assert g2["reference_default"]["area"] == g2["reference"]["area"] and g2["reference"]["custom"] == []
    # a run carries the convention into its parameters, CSV header and expected-error reason
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["params"]["part_type"] == "body" and s["params"]["area_method"] == "silhouette"
    assert s["params"]["reference_area"] == pytest.approx(0.001, rel=2e-2)
    assert "treated as a body" in " ".join(s["result"]["expected"]["cd"]["reasons"])
    assert "frontal area" in c.get(f"/api/runs/{rid}/results.csv").text
    md = c.get(f"/api/runs/{rid}/report.md").text
    assert "| Part treated as | a body: CL and CD on its frontal area" in md
    assert "measured from the part's surface" in md
    # back to WindLab's guess
    g3 = c.post(f"/api/geometry/{gid}/reference", json={"part_type": "auto"}).json()
    assert g3["part_type"]["chosen"] is None and g3["part_type"]["used"] == "wing"
    assert g3["reference"]["area"] == pytest.approx(0.06, rel=1e-2)


def test_choice_survives_a_turn_and_resets_typed_values(env, tmp_path):
    _, c = env
    g = _upload_box(c, tmp_path, (300, 200, 5))
    gid = g["geometry_id"]
    c.post(f"/api/geometry/{gid}/reference", json={"area": 0.5})
    g2 = c.post(f"/api/geometry/{gid}/reference", json={"part_type": "body"}).json()
    assert g2["reference"]["custom"] == [] and g2["reference"]["area"] == pytest.approx(0.001, rel=2e-2)
    g3 = c.post(f"/api/geometry/{gid}/reference", json={"part_type": "body", "length": 0.25}).json()
    assert g3["reference"]["length"] == 0.25 and g3["reference"]["custom"] == ["length"]
    assert g3["reference"]["how"].startswith("length set by you; otherwise frontal area")
    g4 = c.post(f"/api/geometry/{gid}/orient", json={"rotate": "x"}).json()  # standing on its edge now
    assert g4["size_m"] == pytest.approx([0.3, 0.005, 0.2])
    assert g4["part_type"]["chosen"] == "body" and g4["part_type"]["used"] == "body"
    assert g4["reference"]["area"] == pytest.approx(0.001, rel=2e-2)  # frontal: 5 mm x 200 mm
    assert g4["areas"]["planform"] == pytest.approx(0.0015, rel=2e-2)


def test_built_in_shapes_report_their_kind_and_refuse_a_switch(env):
    _, c = env
    w = naca(c).json()
    assert w["part_type"] == {"detected": "wing", "chosen": None, "used": "wing"} and w["areas"] is None
    r = c.post(f"/api/geometry/{w['geometry_id']}/reference", json={"part_type": "body"})
    assert r.status_code == 400 and "only an uploaded part" in r.json()["detail"]
    b = c.post("/api/geometry/builtin", json={"shape": "sphere", "diameter": 0.2}).json()
    assert b["part_type"]["used"] == "body"
    assert c.post(f"/api/geometry/{b['geometry_id']}/reference", json={"part_type": "plane"}).status_code == 422


def test_body_default_matches_the_frontal_silhouette(env, tmp_path):
    _, c = env
    g = _upload_box(c, tmp_path, (300, 300, 300))  # a cube: a body on its frontal area
    assert g["part_type"]["used"] == "body" and g["reference"]["area"] == pytest.approx(0.09, rel=1e-2)
    assert g["reference"]["length"] == pytest.approx(0.3)
    assert g["reference"]["how"].startswith("frontal area (the silhouette seen from the front")
