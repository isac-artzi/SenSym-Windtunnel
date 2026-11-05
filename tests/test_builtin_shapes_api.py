"""Built-in bodies through the API: POST /api/geometry/builtin, the catalogue in /api/config, runs on them."""

from __future__ import annotations

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver
from windlab.app import Settings, create_app


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def test_catalogue_in_config(env):
    _, c = env
    cat = c.get("/api/config").json()["builtin_shapes"]
    assert [s["id"] for s in cat] == ["sphere", "cylinder", "plate", "cube", "teardrop"]
    assert cat[0]["params"][0]["id"] == "diameter" and "0.47" in cat[0]["description"]


@pytest.mark.parametrize("body, cls, area, length", [
    ({"shape": "sphere", "diameter": 0.2}, "bluff-smooth", 3.14159 * 0.01, 0.2),
    ({"shape": "cylinder", "diameter": 0.15, "length": 0.45}, "bluff-smooth", 0.0675, 0.15),
    ({"shape": "plate", "height": 0.2, "width": 0.3}, "bluff", 0.06, 0.2),
    ({"shape": "cube", "side": 0.2}, "bluff", 0.04, 0.2),
    ({"shape": "teardrop", "diameter": 0.2, "fineness": 3}, "streamlined-body", 3.14159 * 0.01, 0.2),
])
def test_make_builtin_body(env, body, cls, area, length):
    _, c = env
    r = c.post("/api/geometry/builtin", json=body)
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["usable"] and g["kind"] == "builtin" and g["class"] == cls
    assert g["reference"]["area"] == pytest.approx(area, rel=1e-3) and g["reference"]["length"] == length
    assert "frontal area" in g["reference"]["how"] and g["builtin"]["shape"] == body["shape"]
    assert g["size_m"][0] > 0 and not g["size_suspicious"]
    assert c.get(f"/api/geometry/{g['geometry_id']}/preview").status_code == 200


@pytest.mark.parametrize("body, word", [
    ({"shape": "torus", "diameter": 0.2}, "built-in shapes"),
    ({"shape": "sphere", "diameter": 50}, "between 0.02 m and 2 m"),
    ({"shape": "sphere", "radius": 0.1}, "Unknown setting"),
    ({"shape": "sphere", "diameter": "big"}, "numbers"),
    ({"shape": "cylinder", "diameter": 0.05, "length": 1.5}, "times its diameter"),
    ({"shape": "teardrop", "diameter": 0.2, "fineness": 12}, "fineness"),
    ({"shape": "sphere", "diameter": 0.2, "a": 1, "b": 2, "c": 3, "d": 4}, "numbers"),
])
def test_bad_builtin_requests(env, body, word):
    _, c = env
    r = c.post("/api/geometry/builtin", json=body)
    assert r.status_code == 400, r.text
    assert word in r.json()["detail"]


def test_builtin_body_runs_and_is_unvalidated_for_now(env):
    a, c = env
    g = c.post("/api/geometry/builtin", json={"shape": "cube", "side": 0.2}).json()
    rid = c.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "done" and s["params"]["geometry_class"] == "bluff"
    assert s["params"]["reference_area"] == pytest.approx(0.04)
    assert s["result"]["expected"]["cd"]["status"] in ("validated", "unvalidated")
    # built-in bodies cannot be turned (they already face the wind)
    r = c.post(f"/api/geometry/{g['geometry_id']}/orient", json={"rotate": "x"})
    assert r.status_code == 400 and "already lined up" in r.json()["detail"]
