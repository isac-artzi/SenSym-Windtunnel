"""Comparing 2-5 finished runs: GET /api/compare and /api/compare.csv."""

from __future__ import annotations

import uuid

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from tests.test_sweep import check_comment_lines, read_csv_like_pandas
from windlab.app import Settings, create_app
from windlab.app.runs import compare_warnings


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def _single(c, gid, alpha=8, speed=20):
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": speed, "alpha_deg": alpha})
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def _sweep(c, gid, angles=(-4, 4, 12), speed=20):
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": speed, "kind": "sweep", "angles": list(angles)})
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def test_compare_two_wings_single_and_sweep(env):
    a, c = env
    g12 = naca(c).json()["geometry_id"]
    g24 = naca(c, code="2412").json()["geometry_id"]
    r1, r2, r3 = _single(c, g12), _single(c, g24), _sweep(c, g24)
    assert a.fastapi.state.runs.wait_idle(30)
    res = c.get("/api/compare", params={"runs": f"{r1},{r2},{r3}"})
    assert res.status_code == 200, res.text
    d = res.json()
    assert [r["letter"] for r in d["runs"]] == ["A", "B", "C"]
    assert [r["run_id"] for r in d["runs"]] == [r1, r2, r3]
    assert d["runs"][0]["name"].startswith("NACA 0012") and d["runs"][1]["name"].startswith("NACA 2412")
    assert d["runs"][0]["kind"] == "single" and len(d["runs"][0]["points"]) == 1
    p = d["runs"][0]["points"][0]
    assert p["alpha_deg"] == 8 and p["cl"] == pytest.approx(0.45) and p["l_over_d"] == pytest.approx(9.0)
    assert [q["alpha_deg"] for q in d["runs"][2]["points"]] == [-4, 4, 12]
    # same speed, chord and area: directly comparable, and the page is told so
    assert len(d["warnings"]) == 1 and "compared directly" in d["warnings"][0]
    assert d["label"].startswith("Uncalibrated")


def test_compare_warns_about_speed_and_notes_planform_areas(env):
    a, c = env
    g1 = naca(c).json()["geometry_id"]
    g2 = naca(c, chord=0.6, span=1.2).json()["geometry_id"]  # 4x the area, twice the reference length
    r1, r2, r3 = _single(c, g1), _single(c, g2), _single(c, g1, speed=40)
    assert a.fastapi.state.runs.wait_idle(30)
    w = c.get("/api/compare", params={"runs": f"{r1},{r2}"}).json()["warnings"]
    # two wings, each on its own planform area: the standard comparison, an informational note
    assert any("its own planform area" in x and "compared directly" in x for x in w)
    assert not any("not directly comparable" in x for x in w)
    assert any("different Reynolds numbers" in x and "reference lengths" in x for x in w)
    w = c.get("/api/compare", params={"runs": f"{r1},{r3}"}).json()["warnings"]
    assert any("different airspeeds" in x and "20 m/s" in x and "40 m/s" in x for x in w)
    assert not any("reference area" in x for x in w)


def test_compare_bodies_on_their_own_frontal_areas_is_the_standard_comparison(env):
    """The Streamlining lesson: five bodies, each CD on its own frontal area (task 010, step 6)."""
    a, c = env
    plate = c.post("/api/geometry/builtin", json={"shape": "plate", "height": 0.2, "width": 0.2}).json()
    sphere = c.post("/api/geometry/builtin", json={"shape": "sphere", "diameter": 0.2}).json()
    cyl = c.post("/api/geometry/builtin", json={"shape": "cylinder", "diameter": 0.15, "length": 0.45}).json()
    ids = [_single(c, g["geometry_id"], alpha=0) for g in (plate, sphere, cyl)]
    assert a.fastapi.state.runs.wait_idle(30)
    d = c.get("/api/compare", params={"runs": ",".join(ids)}).json()
    assert all(r["reference_how"].startswith("frontal area") for r in d["runs"])
    note = next(x for x in d["warnings"] if "frontal area" in x)
    assert "handbook" in note and "compared directly" in note and "not directly comparable" not in note


def test_compare_warns_on_mixed_or_hand_set_reference_conventions(env):
    a, c = env
    wing = naca(c).json()["geometry_id"]
    plate = c.post("/api/geometry/builtin", json={"shape": "plate", "height": 0.2, "width": 0.2}).json()["geometry_id"]
    custom = naca(c, chord=0.3, span=0.6).json()["geometry_id"]
    assert c.post(f"/api/geometry/{custom}/reference", json={"area": 0.05}).status_code == 200
    r_wing, r_plate, r_custom = _single(c, wing), _single(c, plate, alpha=0), _single(c, custom)
    assert a.fastapi.state.runs.wait_idle(30)
    w = c.get("/api/compare", params={"runs": f"{r_wing},{r_plate}"}).json()["warnings"]
    assert any("different conventions" in x and "planform area" in x and "frontal area" in x
               and "not directly comparable" in x for x in w)
    w = c.get("/api/compare", params={"runs": f"{r_wing},{r_custom}"}).json()["warnings"]
    assert any("set by you" in x and "not directly comparable" in x for x in w)


def test_reference_convention_words():
    from windlab.app.runs import reference_convention

    assert reference_convention("frontal area and diameter") == "frontal area"
    assert reference_convention("mean aerodynamic chord and planform area (chord x span)") == "planform area"
    assert reference_convention("length along the wind (x) and plan-view box area (length x width)") == \
        "plan-view box area"
    assert reference_convention("area set by you; otherwise frontal area and height") == "set by you"
    assert reference_convention(None) == "unknown"


def test_compare_warnings_rule_of_thumb():
    base = {"letter": "A", "airspeed": 20.0, "reynolds": 4.1e5, "reference_area": 0.18, "preset": "quick"}
    same = [base, {**base, "letter": "B", "reference_area": 0.1801}]  # within 1 %: the same
    assert "compared directly" in compare_warnings(same)[0]


def test_compare_validates_the_run_ids(env):
    a, c = env
    gid = naca(c).json()["geometry_id"]
    r1 = _single(c, gid)
    assert a.fastapi.state.runs.wait_idle(20)
    for runs, code, words in (
        (r1, 400, "Choose 2 to 5"),
        (f"{r1},{r1}", 400, "Choose 2 to 5"),  # the same run twice is one run
        (f"{r1},../etc/passwd", 400, "not a valid run id"),
        (f"{r1},{uuid.uuid4()}", 404, "was not found"),
        (",".join([r1] + [str(uuid.uuid4()) for _ in range(4)]), 404, "was not found"),  # 5 ids fit
        (",".join([r1] + [str(uuid.uuid4()) for _ in range(5)]), 422, "not valid"),  # too long: 6 ids
    ):
        res = c.get("/api/compare", params={"runs": runs})
        assert res.status_code == code, (runs, res.text)
        assert words in res.json()["detail"]
    assert c.get("/api/compare").status_code == 422
    six = ",".join(str(uuid.uuid4())[:8] for _ in range(6))
    assert c.get("/api/compare", params={"runs": six}).status_code == 400


def test_compare_five_runs(env):
    """Five at once (the Streamlining lesson's five bodies): letters A to E, a fifth marker on the page."""
    a, c = env
    gid = naca(c).json()["geometry_id"]
    ids = [_single(c, gid, alpha=k) for k in (0, 2, 4, 6, 8)]
    assert a.fastapi.state.runs.wait_idle(60)
    d = c.get("/api/compare", params={"runs": ",".join(ids)}).json()
    assert [r["letter"] for r in d["runs"]] == ["A", "B", "C", "D", "E"]
    assert "A, B, C, D and E" in d["warnings"][0]
    assert c.get("/api/compare.csv", params={"runs": ",".join(ids)}).status_code == 200


def test_compare_refuses_unfinished_runs(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver(fail_stage="simpleFoam")))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    r1, r2 = _single(c, gid), _single(c, gid, alpha=4)
    assert a.fastapi.state.runs.wait_idle(20)
    res = c.get("/api/compare", params={"runs": f"{r1},{r2}"})
    assert res.status_code == 409 and "Only finished runs" in res.json()["detail"]
    a.fastapi.state.runs.stop()


def test_compare_csv(env):
    a, c = env
    g12 = naca(c).json()["geometry_id"]
    g24 = naca(c, code="2412").json()["geometry_id"]
    r1, r2 = _sweep(c, g12, (0, 8)), _single(c, g24)
    assert a.fastapi.state.runs.wait_idle(30)
    res = c.get("/api/compare.csv", params={"runs": f"{r1},{r2}"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert 'filename="windlab-compare-' in res.headers["content-disposition"]
    check_comment_lines(res.text)
    head, rows = read_csv_like_pandas(res.text)
    assert head[:2] == ["run", "shape"] and "CL [-]" in head and "L/D [-]" in head
    assert [(r[0], float(r[7])) for r in rows] == [("A", 0.0), ("A", 8.0), ("B", 8.0)]
    assert rows[2][1].startswith("NACA 2412")
    assert "# note: Runs A and B used the same airspeed" in res.text
