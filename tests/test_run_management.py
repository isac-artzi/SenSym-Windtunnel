"""Run management: cancel, run history, delete, disk housekeeping."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from windlab.app import Settings, create_app
from windlab.app import housekeeping as hk
from windlab.runner import RunCancelled, terminate_group


def _app(tmp_path, solver=None, **kw):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver or FakeSolver(), **kw))
    return a, make_client(a)


def _submit(c, gid, alpha=0.0):
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": alpha})
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def _wait_status(c, rid, status, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = c.get(f"/api/runs/{rid}").json()
        if s["status"] == status:
            return s
        time.sleep(0.01)
    raise AssertionError(f"run {rid} never reached {status}: {s['status']}")


# --------------------------------------------------------------------------- cancel


def test_cancel_running_run_frees_the_queue(tmp_path):
    solver = FakeSolver(delay=0.4)
    a, c = _app(tmp_path, solver)
    gid = naca(c).json()["geometry_id"]
    rid = _submit(c, gid)
    _wait_status(c, rid, "running")
    r = c.post(f"/api/runs/{rid}/cancel")
    assert r.status_code == 200, r.text
    s = _wait_status(c, rid, "cancelled")
    assert s["stage"] == "cancelled" and "cancelled" in s["stage_label"] and s["error"] is None
    # the queue is free: the next run goes through
    solver.delay = 0.0
    rid2 = _submit(c, gid, 5)
    assert a.fastapi.state.runs.wait_idle(10)
    assert c.get(f"/api/runs/{rid2}").json()["status"] == "done"
    # cancelling twice is harmless; a finished run can't be cancelled
    assert c.post(f"/api/runs/{rid}/cancel").status_code == 200
    assert c.post(f"/api/runs/{rid2}/cancel").status_code == 409
    a.fastapi.state.runs.stop()


def test_cancel_queued_run_is_skipped(tmp_path):
    solver = FakeSolver(delay=0.1)
    a, c = _app(tmp_path, solver)
    gid = naca(c).json()["geometry_id"]
    first, second = _submit(c, gid), _submit(c, gid, 5)
    s = c.post(f"/api/runs/{second}/cancel").json()
    assert s["status"] == "cancelled"  # at once, without waiting for the first run
    assert a.fastapi.state.runs.wait_idle(10)
    assert c.get(f"/api/runs/{first}").json()["status"] == "done"
    assert [p.name for p in solver.calls] == [first]
    # the cancelled status survives a restart
    a.fastapi.state.runs.stop()
    _, c2 = _app(tmp_path)
    assert c2.get(f"/api/runs/{second}").json()["status"] == "cancelled"


def test_cancel_unknown_or_bad_id(tmp_path):
    _, c = _app(tmp_path)
    assert c.post(f"/api/runs/{uuid.uuid4()}/cancel").status_code == 404
    assert c.post("/api/runs/../../etc/cancel").status_code in (400, 404)


def test_terminate_group_stops_every_process_in_the_group():
    # a parent with two children in its own session, like mpirun and its ranks
    proc = subprocess.Popen(["bash", "-c", "sleep 60 & sleep 60 & wait"], start_new_session=True)
    time.sleep(0.3)
    pgid = os.getpgid(proc.pid)
    t0 = time.monotonic()
    terminate_group(proc, grace=2.0)
    assert time.monotonic() - t0 < 5 and proc.poll() is not None
    time.sleep(0.2)
    assert _live_members(pgid) == []  # nothing left running in the group (zombies don't count)


def test_terminate_group_also_stops_children_in_other_groups():
    # OpenMPI puts each rank in its own process group; one that ignores SIGTERM must still go
    script = "setsid bash -c 'trap \"\" TERM; while :; do sleep 0.2; done' & echo $! ; wait"
    proc = subprocess.Popen(["bash", "-c", script], start_new_session=True, stdout=subprocess.PIPE, text=True)
    child = int(proc.stdout.readline())
    time.sleep(0.3)
    assert os.getpgid(child) != os.getpgid(proc.pid)
    terminate_group(proc, grace=0.5)
    time.sleep(0.2)
    state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0] \
        if Path(f"/proc/{child}").exists() else "gone"
    assert state in ("Z", "X", "gone")


def _live_members(pgid: int) -> list[int]:
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path(f"/proc/{d}/stat").read_text()
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[1].split()
        if int(fields[2]) == pgid and fields[0] != "Z":
            out.append(int(d))
    return out


def test_runner_stage_raises_cancelled(tmp_path, monkeypatch):
    from windlab import runner

    monkeypatch.setattr(runner, "foam_env", lambda: dict(os.environ))
    ev = threading.Event()
    threading.Timer(0.3, ev.set).start()
    rep = runner.RunReport(run_id="x", case_dir=str(tmp_path), n_procs=1)
    t0 = time.monotonic()
    with pytest.raises(RunCancelled) as e:
        runner._run_stage(tmp_path, "simpleFoam", ["sleep", "30"], rep, cancel=ev)
    assert e.value.stage == "simpleFoam" and time.monotonic() - t0 < 5
    with pytest.raises(RunCancelled):  # already cancelled: the next stage never starts
        runner._run_stage(tmp_path, "checkMesh", ["sleep", "30"], rep, cancel=ev)


# --------------------------------------------------------------------------- history / delete


def test_run_history_lists_newest_first(tmp_path):
    a, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    ids = [_submit(c, gid, al) for al in (0, 4)]
    assert a.fastapi.state.runs.wait_idle(10)
    runs = c.get("/api/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == ids[::-1]
    r = runs[0]
    assert r["status"] == "done" and r["alpha_deg"] == 4 and r["airspeed"] == 20
    assert r["cl"] == pytest.approx(0.45) and r["cd"] == pytest.approx(0.05)
    assert r["geometry_name"].startswith("NACA 0012") and r["created"] > 0 and r["kind"] == "single"
    # a fresh server sees the same history from disk
    a.fastapi.state.runs.stop()
    _, c2 = _app(tmp_path)
    assert [r["run_id"] for r in c2.get("/api/runs").json()["runs"]] == ids[::-1]


def test_delete_run_removes_its_folder(tmp_path):
    solver = FakeSolver(delay=0.3)
    a, c = _app(tmp_path, solver)
    gid = naca(c).json()["geometry_id"]
    rid = _submit(c, gid)
    _wait_status(c, rid, "running")
    r = c.delete(f"/api/runs/{rid}")
    assert r.status_code == 409 and "Stop this run" in r.json()["detail"]
    assert a.fastapi.state.runs.wait_idle(10)
    folder = a.fastapi.state.runs.root / rid
    assert folder.is_dir()
    assert c.delete(f"/api/runs/{rid}").status_code == 200
    assert not folder.exists()
    assert c.get(f"/api/runs/{rid}").status_code == 404
    assert c.get("/api/runs").json()["runs"] == []
    assert c.delete(f"/api/runs/{uuid.uuid4()}").status_code == 404
    assert c.delete("/api/runs/not-an-id").status_code == 400
    a.fastapi.state.runs.stop()


def test_delete_needs_same_origin(tmp_path):
    a, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    rid = _submit(c, gid)
    assert a.fastapi.state.runs.wait_idle(10)
    r = c.delete(f"/api/runs/{rid}", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert (a.fastapi.state.runs.root / rid).is_dir()


# --------------------------------------------------------------------------- housekeeping


def test_finished_case_is_slimmed_but_results_stay(tmp_path):
    a, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    rid = _submit(c, gid)
    assert a.fastapi.state.runs.wait_idle(10)
    case = a.fastapi.state.runs.root / rid
    assert not list(case.glob("processor*")) and not (case / "350").exists()
    assert not (case / "postProcessing" / "bodySurface").exists()
    assert (case / "postProcessing" / "forceCoeffs1").is_dir()
    assert (case / "system" / "controlDict").is_file() and (case / "windlab_status.json").is_file()
    s = c.get(f"/api/runs/{rid}").json()
    assert s["history"][-1][0] == 300 and s["surface_available"]
    assert c.get(f"/api/runs/{rid}/surface").status_code == 200


def test_keep_at_most_n_finished_runs(tmp_path):
    a, c = _app(tmp_path, keep_runs=2)
    gid = naca(c).json()["geometry_id"]
    ids = []
    for al in (0, 2, 4):
        ids.append(_submit(c, gid, al))
        assert a.fastapi.state.runs.wait_idle(10)
        time.sleep(0.01)
    left = [r["run_id"] for r in c.get("/api/runs").json()["runs"]]
    assert left == ids[:0:-1]  # the oldest went first
    assert not (a.fastapi.state.runs.root / ids[0]).exists()
    a.fastapi.state.runs.stop()


def _fake_run(root: Path, status: str, finished: float, size: int) -> str:
    rid = str(uuid.uuid4())
    d = root / rid
    d.mkdir(parents=True)
    (d / "windlab_status.json").write_text(json.dumps({"status": status, "finished": finished,
                                                       "created": finished - 10, "geometry_id": None}))
    (d / "blob").write_bytes(b"\0" * size)
    return rid


def test_prune_runs_disk_cap_oldest_first_never_active(tmp_path):
    runs = tmp_path / "data" / "runs"
    old = _fake_run(runs, "done", 100, 4000)
    running = _fake_run(runs, "running", 50, 4000)  # oldest of all, but still active
    mid = _fake_run(runs, "failed", 200, 4000)
    new = _fake_run(runs, "cancelled", 300, 4000)
    deleted = hk.prune_runs(runs, tmp_path / "data", active={running}, keep=20, cap_bytes=9000)
    assert deleted == [old, mid]
    assert {p.name for p in runs.iterdir()} == {running, new}
    # nothing more to delete: only the active run would be left to remove
    assert hk.prune_runs(runs, tmp_path / "data", active={running, new}, keep=0, cap_bytes=0) == []


def test_prune_runs_ignores_foreign_folders_and_symlinks(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "not-a-run").mkdir()
    outside = tmp_path / "precious"
    outside.mkdir()
    (runs / str(uuid.uuid4())).symlink_to(outside)
    assert hk.prune_runs(runs, tmp_path, keep=0, cap_bytes=0) == []
    assert outside.is_dir() and (runs / "not-a-run").is_dir()


def test_unused_old_geometry_is_removed(tmp_path):
    a, c = _app(tmp_path)
    store = a.fastapi.state.geometry
    used = naca(c).json()["geometry_id"]
    unused_old = naca(c, chord=0.2).json()["geometry_id"]
    unused_new = naca(c, chord=0.25).json()["geometry_id"]
    _submit(c, used)
    assert a.fastapi.state.runs.wait_idle(10)
    eight_days_ago = time.time() - 8 * 86400
    for gid in (used, unused_old):
        meta = store.get(gid)
        meta["created"] = meta["last_used"] = eight_days_ago
        store.save(meta)
    out = a.fastapi.state.runs.housekeep()
    assert out["shapes_removed"] == [unused_old]
    assert c.get(f"/api/geometry/{unused_old}").status_code == 404
    for gid in (used, unused_new):  # used by a run / recent
        assert c.get(f"/api/geometry/{gid}").status_code == 200


def test_stale_incoming_uploads_removed(tmp_path):
    inc = tmp_path / "incoming"
    inc.mkdir()
    old, new = inc / "upload-a.stl", inc / "upload-b.stl"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    assert hk.prune_incoming(inc) == 1 and not old.exists() and new.exists()


def test_slim_case_keeps_inputs_and_results(tmp_path):
    case = tmp_path / "case"
    for p in ("processor0/0", "processor0/constant/polyMesh", "120", "constant/polyMesh",
              "postProcessing/forceCoeffs1/0", "postProcessing/bodySurface/120", "system", "0.orig",
              "sweep/p0/postProcessing/forceCoeffs1/0", "sweep/p0/postProcessing/midPlane/120"):
        (case / p).mkdir(parents=True, exist_ok=True)
        (case / p / "f").write_bytes(b"\0" * 100)
    for f in ("windlab_status.json", "windlab_surface.bin", "log.simpleFoam", "constant/triSurface/body.stl"):
        (case / f).parent.mkdir(parents=True, exist_ok=True)
        (case / f).write_text("x")
    freed = hk.slim_case(case)
    assert freed >= 500
    left = {str(p.relative_to(case)) for p in case.rglob("f")}
    assert left == {"postProcessing/forceCoeffs1/0/f", "system/f", "0.orig/f",
                    "sweep/p0/postProcessing/forceCoeffs1/0/f"}
    for f in ("windlab_status.json", "windlab_surface.bin", "log.simpleFoam", "constant/triSurface/body.stl"):
        assert (case / f).exists()
