"""Per-machine speed records and the run-time estimate shown before a run (windlab.speed)."""

from __future__ import annotations

import json

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from windlab import speed
from windlab.app import Settings, create_app
from windlab.case_builder import Preset

GB = 2**30


def test_defaults_before_any_run_are_conservative(tmp_path):
    e = speed.estimate(tmp_path, "quick", 4)
    assert e["basis"] == "default" and e["seconds"] > 150 and e["text"].startswith("about")
    # the Windows test machine's cold Quick run took 197 s (old stop rule) on 4 ranks: the default
    # must not promise less than that
    assert e["seconds"] >= 197
    s = speed.estimate(tmp_path, "standard", 4)
    assert s["seconds"] > 2 * e["seconds"] and s["cells"] > 2 * e["cells"]
    assert speed.estimate(tmp_path, "quick", 8)["seconds"] < e["seconds"]  # more cores: faster
    assert speed.estimate(tmp_path, "quick", 4, n_angles=6)["seconds"] > 4 * e["seconds"]


def test_records_scale_the_estimate(tmp_path):
    rec = speed.record_run(tmp_path, preset="quick", n_procs=4, cells=372_000, iterations=245, solver_seconds=41.0,
                           mesh_seconds=12.0, total_seconds=55.0)
    assert rec["solve_s_per_cell_iteration"] == pytest.approx(41 / (372_000 * 245))
    assert rec["mesh_s_per_cell"] == pytest.approx(12 / 372_000)
    sp = speed.machine_speed(tmp_path)
    assert sp["basis"] == "measured" and sp["runs"] == 1 and sp["quick_seconds"] == 55
    e = speed.estimate(tmp_path, "quick", 4, quick_cells=372_000)
    # 1.25 x (12 + 300 iterations x 41/245 s) = 78 s
    assert e["seconds"] == pytest.approx(1.25 * (12 + 300 * 41 / 245), abs=2) and e["basis"] == "measured"
    assert e["text"] == "about 2 minutes"
    # the same run measured on 8 ranks is normalised to 4: a slower per-rank machine
    rec8 = speed.record_run(tmp_path, preset="quick", n_procs=8, cells=372_000, iterations=245, solver_seconds=41.0,
                            mesh_seconds=12.0, total_seconds=55.0)
    assert rec8["solve_s_per_cell_iteration"] > rec["solve_s_per_cell_iteration"]
    # a warm-started run with a reused mesh teaches the solver speed but not the meshing speed
    warm = speed.record_run(tmp_path, preset="quick", n_procs=4, cells=372_000, iterations=165, solver_seconds=28.0,
                            mesh_seconds=None, total_seconds=30.0, warm=True, mesh_reused=True)
    assert "mesh_s_per_cell" not in warm and "solve_s_per_cell_iteration" in warm
    assert speed.machine_speed(tmp_path)["quick_seconds"] == 55  # warm runs don't count as "a cold Quick run"
    assert speed.record_run(tmp_path, preset="quick", n_procs=4, cells=None, iterations=1, solver_seconds=1,
                            mesh_seconds=1) is None
    assert len(speed.records(tmp_path)) == 3
    for _ in range(50):
        speed.record_run(tmp_path, preset="quick", n_procs=4, cells=10_000, iterations=10, solver_seconds=1,
                         mesh_seconds=1)
    assert len(speed.records(tmp_path)) == speed.MAX_RECORDS


def test_damaged_records_file_is_ignored(tmp_path):
    (tmp_path / speed.RECORDS_FILE).write_text("{not json")
    assert speed.records(tmp_path) == [] and speed.machine_speed(tmp_path)["basis"] == "default"


@pytest.mark.parametrize("seconds, text", [(30, "under a minute"), (60, "about 1 minute"), (61, "about 2 minutes"),
                                           (3000, "about 50 minutes"), (5400, "about 1.5 hours")])
def test_duration_text(seconds, text):
    assert speed.duration_text(seconds) == text


def test_detailed_needs_memory_and_a_fast_quick_run(tmp_path):
    assert speed.available_presets(tmp_path, 32 * GB) == [Preset.QUICK, Preset.STANDARD]
    speed.record_run(tmp_path, preset="quick", n_procs=4, cells=372_000, iterations=245, solver_seconds=41.0,
                     mesh_seconds=12.0, total_seconds=55.0)
    assert speed.detailed_allowed(tmp_path, 32 * GB)
    assert not speed.detailed_allowed(tmp_path, 16 * GB)
    speed.record_run(tmp_path / "slow", preset="quick", n_procs=4, cells=372_000, iterations=390, solver_seconds=160,
                     mesh_seconds=34.0, total_seconds=197.0)
    assert not speed.detailed_allowed(tmp_path / "slow", 32 * GB)
    assert Preset.DETAILED in speed.available_presets(tmp_path, 32 * GB)


# --------------------------------------------------------------------------- through the API


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver(delay=0.02), n_procs=4))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def test_estimate_endpoint_learns_from_finished_runs(env):
    a, c = env
    e = c.get("/api/estimate").json()
    assert e["basis"] == "default" and set(e["presets"]) >= {"quick", "standard"} and e["n_angles"] == 1
    assert e["presets"]["quick"]["text"].startswith("about") and e["n_procs"] == 4
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(10)
    assert c.get(f"/api/runs/{rid}").json()["status"] == "done"
    recs = speed.records(a.fastapi.state.runs.data_root)
    assert len(recs) == 1 and recs[0]["cells"] == 371_000 and recs[0]["iterations"] == 300
    e = c.get("/api/estimate", params={"geometry_id": gid, "n_angles": 6}).json()
    assert e["basis"] == "measured" and e["quick_cells"] == 371_000 and e["n_angles"] == 6
    assert e["presets"]["standard"]["seconds"] > e["presets"]["quick"]["seconds"]
    assert c.get("/api/estimate", params={"n_angles": 0}).status_code == 422
    assert c.get("/api/estimate", params={"geometry_id": "not-an-id"}).json()["quick_cells"] is None


def test_standard_runs_and_detailed_is_refused_without_the_gate(env):
    a, c = env
    gid = naca(c).json()["geometry_id"]
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4, "preset": "standard"})
    assert r.status_code == 202, r.text
    assert r.json()["params"]["preset"] == "standard" and r.json()["params"]["max_iterations"] == 2000
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4, "preset": "detailed"})
    assert r.status_code == 400 and "not available on this computer" in r.json()["detail"]
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4, "preset": "check"})
    assert r.status_code == 400
    assert a.fastapi.state.runs.wait_idle(10)
    cfg = c.get("/api/config").json()
    assert [p["id"] for p in cfg["presets"]] == ["quick", "standard"]


def test_detailed_appears_on_a_big_fast_machine(tmp_path, monkeypatch):
    from windlab import machine

    monkeypatch.setattr(machine, "meminfo", lambda: {"total": 32 * GB, "available": 20 * GB})
    data = tmp_path / "d"
    data.mkdir()
    speed.record_run(data, preset="quick", n_procs=4, cells=372_000, iterations=245, solver_seconds=41.0,
                     mesh_seconds=12.0, total_seconds=55.0)
    a = create_app(Settings(data_dir=data, runner=FakeSolver(), n_procs=4))
    c = make_client(a)
    cfg = c.get("/api/config").json()
    assert [p["id"] for p in cfg["presets"]] == ["quick", "standard", "detailed"]
    assert "detailed" in c.get("/api/estimate").json()["presets"]
    gid = naca(c).json()["geometry_id"]
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4, "preset": "detailed"})
    assert r.status_code == 202, r.text
    assert a.fastapi.state.runs.wait_idle(10)
    assert json.loads((a.fastapi.state.runs.root / r.json()["run_id"] / "windlab_case.json").read_text())[
        "preset"] == "detailed"
    a.fastapi.state.runs.stop()
