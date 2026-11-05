"""Expected-error ranges (PRD RES-2): the validation table, the lookup, and where the app shows them."""

from __future__ import annotations

import json

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, cube_bytes, naca
from tests.test_sweep import read_csv_like_pandas
from windlab import expected_error as ee
from windlab.app import Settings, create_app

RE_WING = 20 * 0.3 / 1.4607e-5  # the default wing at 20 m/s: 4.1e5


def csv_rows(text: str) -> list[dict]:
    header, rows = read_csv_like_pandas(text)
    return [dict(zip(header, r, strict=True)) for r in rows]


def test_table_is_versioned_and_complete():
    t = ee.load_table()
    assert t["version"].startswith("2026-")
    q = t["classes"]["streamlined-lifting"]["presets"]["quick"]
    assert q["envelope"]["reynolds"][0] < RE_WING < q["envelope"]["reynolds"][1]
    assert q["cl"]["low"] < 0 < q["cl"]["high"] and q["cl"]["basis"]
    for band in q["cd"]["bands"]:
        assert band["numerical"]["low"] < 0 and band["basis"]
        assert band["model"]["low"] < 0 <= band["model"]["high"]
    for cls in ee.CLASSES:
        assert cls in t["classes"]
    # the evidence agrees with the validation documents
    errors = {e["case"]: e["error"] for e in q["cd"]["evidence"]}
    assert errors["3D NACA 0012 wing, 0 deg: Quick vs Richardson-extrapolated CD"] == pytest.approx(0.094)
    assert errors["2D NACA 0012, Re 6e6, 10 deg: WindLab vs NASA TMR CD"] == pytest.approx(0.003)


def test_lift_range_is_small_and_symmetric():
    r = ee.coefficient_range("cl", 0.451, "streamlined-lifting", "quick", RE_WING, 10.0)
    assert r["status"] == "validated"
    assert r["low"] == pytest.approx(0.451 * 0.96) and r["high"] == pytest.approx(0.451 * 1.04)
    assert r["text"] == "likely 0.433 to 0.469"
    assert "Lift is the number to trust" in r["why"] and "4 of 4 comparisons" in r["why"]
    assert "2026-" in r["why"]


def test_drag_range_is_asymmetric_quick_over_predicts():
    r = ee.coefficient_range("cd", 0.0171, "streamlined-lifting", "quick", RE_WING, 0.0)
    assert r["status"] == "validated"
    assert r["high"] == pytest.approx(0.0171 * 1.02)
    assert r["low"] == pytest.approx(0.0171 * 0.88 * 0.60)  # mesh (-12 %) and fully turbulent assumption (-40 %)
    assert "always too high" in r["why"] and "turbulent from the leading edge" in r["why"]
    # the recorded comparisons land inside: 9 % and 32 % high, the 0.71 against measurements, the 2D within 1 %
    assert r["inside"] == r["checks"] >= 8
    # at larger angles induced drag dominates: a narrower band
    r10 = ee.coefficient_range("cd", 0.0558, "streamlined-lifting", "quick", RE_WING, 10.0)
    assert r10["low"] > 0.0558 * 0.70 and r10["high"] == pytest.approx(0.0558 * 1.02)
    # above Re 1e6 the fully turbulent assumption is no longer a bias: only the mesh part remains
    hi_re = ee.coefficient_range("cd", 0.0171, "streamlined-lifting", "quick", 1.1e6, 0.0)
    assert hi_re["low"] == pytest.approx(0.0171 * 0.88)


def test_near_zero_lift_uses_the_floor_and_negative_lift_flips():
    r = ee.coefficient_range("cl", 0.0003, "streamlined-lifting", "quick", RE_WING, 0.0)
    assert r["low"] == pytest.approx(0.0003 - 0.01) and r["high"] == pytest.approx(0.0003 + 0.01)
    assert "−0.010" in r["text"]
    n = ee.coefficient_range("cl", -0.176, "streamlined-lifting", "quick", RE_WING, -4.0)
    assert n["low"] < n["high"] and n["low"] == pytest.approx(-0.176 - 0.01)  # 4 % of 0.176 < the 0.01 floor
    big = ee.coefficient_range("cl", -0.6, "streamlined-lifting", "quick", RE_WING, -12.0)
    assert big["low"] == pytest.approx(-0.6 * 1.04) and big["high"] == pytest.approx(-0.6 * 0.96)


@pytest.mark.parametrize("kwargs, word", [
    ({"geometry_class": "unknown"}, "unknown class"),
    ({"geometry_class": "mixed"}, "no validation evidence yet"),
    ({"geometry_class": "bluff"}, "beyond 2 degrees"),  # bodies: checked at 0 deg only
    ({"alpha_deg": 16.0}, "beyond 12 degrees"),
    ({"reynolds": 5e4}, "Reynolds number is outside"),
    ({"reynolds": 5e6}, "Reynolds number is outside"),
    ({"converged": False}, "did not settle"),
    ({"oscillation": 0.05}, "swinging"),
    ({"preset": "detailed"}, "no validation evidence yet"),
])
def test_outside_the_envelope_is_unvalidated(kwargs, word):
    base = {"geometry_class": "streamlined-lifting", "preset": "quick", "reynolds": RE_WING, "alpha_deg": 8.0,
            "converged": True, "oscillation": 0.004}
    r = ee.coefficient_range("cd", 0.04, **{**base, **kwargs})
    assert r["status"] == "unvalidated" and r["low"] is None and r["high"] is None
    assert r["text"] == ee.UNVALIDATED and any(word in reason for reason in r["reasons"])
    assert "trend only" in r["why"]
    ok = ee.coefficient_range("cd", 0.04, **base)
    assert ok["status"] == "validated"


def test_body_classes_have_their_own_bands():
    plate = ee.coefficient_range("cd", 1.212, "bluff", "quick", 2.7e5, 0.0)
    assert plate["status"] == "validated" and plate["rel_low"] == -0.10 and plate["rel_high"] == 0.10
    assert plate["inside"] == plate["checks"] == 2
    sphere = ee.coefficient_range("cd", 0.252, "bluff-smooth", "quick", 2.7e5, 0.0)
    assert sphere["high"] == pytest.approx(0.504) and "drag crisis" in sphere["why"]
    tear = ee.coefficient_range("cd", 0.068, "streamlined-body", "quick", 2.7e5, 0.0)
    assert tear["low"] < 0.05 < 0.1 < tear["high"]
    # lift of a body: a floor around zero, never a relative band
    assert ee.coefficient_range("cl", 0.083, "bluff-smooth", "quick", 2.7e5, 0.0)["low"] == pytest.approx(0.083 - 0.3)


def test_ratio_range_and_point_helper():
    params = {"preset": "quick", "reynolds": RE_WING, "alpha_deg": 10.0}
    e = ee.expected_for_point({"cl": 0.451, "cd": 0.0558, "converged": True, "cl_pm": 0.001}, params,
                              "streamlined-lifting")
    assert e["cl"]["status"] == e["cd"]["status"] == "validated"
    assert e["l_over_d"]["low"] == pytest.approx(e["cl"]["low"] / e["cd"]["high"])
    assert e["l_over_d"]["high"] == pytest.approx(e["cl"]["high"] / e["cd"]["low"])
    # lift through zero: no L/D range
    e0 = ee.expected_for_point({"cl": 0.0003, "cd": 0.0171, "converged": True}, {**params, "alpha_deg": 0.0},
                               "streamlined-lifting")
    assert "l_over_d" not in e0
    assert ee.geometry_class_of({"geometry_name": "NACA 0012 wing"}) == "streamlined-lifting"
    assert ee.geometry_class_of({"geometry_name": "part.stl"}) == "unknown"
    assert ee.geometry_class_of({"geometry_class": "bluff"}) == "bluff"


# --------------------------------------------------------------------------- in the app


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def test_run_results_carry_ranges(env):
    a, c = env
    g = naca(c).json()
    assert g["class"] == "streamlined-lifting"
    rid = c.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 10}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["params"]["geometry_class"] == "streamlined-lifting"
    e = s["result"]["expected"]
    assert e["cl"]["status"] == "validated" and e["cl"]["text"].startswith("likely 0.43")
    assert e["cd"]["status"] == "validated" and e["cd"]["high"] == pytest.approx(0.05 * 1.02)
    assert e["l_over_d"]["status"] == "validated"
    assert s["result"]["validation_table"] == ee.table_version()
    # the CSV has the range columns
    csv_text = c.get(f"/api/runs/{rid}/results.csv").text
    rows = csv_rows(csv_text)
    assert rows[0]["expected error [validated/unvalidated]"] == "validated"
    assert float(rows[0]["CD likely high [-]"]) == pytest.approx(0.051, abs=1e-3)
    assert "validation table" in csv_text


def test_sweep_points_and_compare_carry_ranges(env):
    a, c = env
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep",
                                    "angles": [0, 8, 16]}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    pts = c.get(f"/api/runs/{rid}").json()["result"]["points"]
    assert pts[0]["expected"]["cl"]["status"] == "validated"
    assert pts[1]["expected"]["cl"]["status"] == "validated"
    assert pts[2]["expected"]["cl"]["status"] == "unvalidated"  # 16 deg: did not settle, beyond 12 deg
    assert any("beyond 12" in r for r in pts[2]["expected"]["cd"]["reasons"])
    rows = csv_rows(c.get(f"/api/runs/{rid}/results.csv").text)
    assert [r["expected error [validated/unvalidated]"] for r in rows] == ["validated", "validated", "unvalidated"]
    assert rows[2]["CL likely low [-]"] == ""
    single = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 8}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    d = c.get("/api/compare", params={"runs": f"{rid},{single}"}).json()
    assert d["runs"][0]["geometry_class"] == "streamlined-lifting"
    assert d["runs"][1]["points"][0]["expected"]["cd"]["status"] == "validated"
    cmp_rows = csv_rows(c.get("/api/compare.csv", params={"runs": f"{rid},{single}"}).text)
    assert cmp_rows[-1]["expected error [validated/unvalidated]"] == "validated"


def test_upload_is_unvalidated(env, tmp_path):
    a, c = env
    g = c.post("/api/geometry", params={"units": "mm"}, content=cube_bytes(tmp_path)).json()
    assert g["class"] == "unknown"
    rid = c.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    e = c.get(f"/api/runs/{rid}").json()["result"]["expected"]
    assert e["cl"]["status"] == "unvalidated" and e["cl"]["text"] == "Unvalidated - treat as a trend only"
    assert any("unknown class" in r for r in e["cl"]["reasons"])


def test_validation_table_endpoint(env):
    _, c = env
    r = c.get("/api/validation-table")
    assert r.status_code == 200
    t = r.json()
    assert t["version"] == ee.table_version() and "streamlined-lifting" in t["classes"]
    assert json.dumps(t)  # plain JSON, no surprises
