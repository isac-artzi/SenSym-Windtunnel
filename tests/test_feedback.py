"""Trial feedback (POST /api/feedback), the anonymous usage log and `windlab feedback export`."""

from __future__ import annotations

import json

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, cube_bytes, naca
from tests.test_sweep import check_comment_lines, read_csv_like_pandas
from windlab import cli, feedback
from windlab.app import Settings, create_app

MACHINE = {"os": "Ubuntu 24.04 LTS", "wsl": True, "logical_cores": 8, "physical_cores": 4, "ram_gb": 15.5,
           "cpu": "Test CPU"}


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c, tmp_path / "d"
    a.fastapi.state.runs.stop()


def test_feedback_is_stored_locally(env):
    a, c, data = env
    r = c.post("/api/feedback", json={"results_sensible": 4, "easy_to_use": 5,
                                      "comment": "The Reynolds number\nconfused me, #1 issue", "name": "A.B."})
    assert r.status_code == 201 and r.json()["saved"] and "this computer only" in r.json()["message"]
    rec = [json.loads(x) for x in (data / "feedback.jsonl").read_text().splitlines()]
    assert len(rec) == 1
    assert rec[0]["results_sensible"] == 4 and rec[0]["easy_to_use"] == 5 and rec[0]["name"] == "A.B."
    assert rec[0]["comment"] == "The Reynolds number confused me, #1 issue"  # one line, otherwise as typed
    # a comment alone is fine; a bad run id is simply not recorded
    r = c.post("/api/feedback", json={"comment": "ok", "run_id": "../../x"})
    assert r.status_code == 201
    assert json.loads((data / "feedback.jsonl").read_text().splitlines()[1])["run_id"] is None


@pytest.mark.parametrize("body, code, words", [
    ({}, 400, "choose a rating or write a comment"),
    ({"comment": "   "}, 400, "choose a rating or write a comment"),
    ({"results_sensible": 6}, 422, "not valid"),
    ({"easy_to_use": 0}, 422, "not valid"),
    ({"comment": "x" * 2001}, 422, "not valid"),
    ({"comment": "hi", "email": "a@b.c"}, 422, "not valid"),  # no other fields accepted
])
def test_feedback_validation(env, body, code, words):
    _, c, _ = env
    r = c.post("/api/feedback", json=body)
    assert r.status_code == code and words in r.json()["detail"]


def test_add_feedback_rejects_non_integers(tmp_path):
    with pytest.raises(ValueError, match="1 to 5"):
        feedback.add_feedback(tmp_path, True, None, "x", "")
    with pytest.raises(ValueError, match="1 to 5"):
        feedback.add_feedback(tmp_path, 3.5, None, "x", "")


def test_usage_log_counts_runs_uploads_cancels_and_errors(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver(fail_stage="snappyHexMesh")))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4})
    c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep", "angles": [0, 4]})
    assert a.fastapi.state.runs.wait_idle(20)
    c.post("/api/geometry", params={"units": "mm", "name": "secret-student-name.stl"},
           content=cube_bytes(tmp_path), headers={"content-type": "application/octet-stream"})
    c.post("/api/geometry", params={"units": "mm", "name": "/home/jo/bad.stl"}, content=b"not an stl at all",
           headers={"content-type": "application/octet-stream"})
    a.fastapi.state.runs.stop()
    events = feedback.UsageLog(tmp_path / "d").read()
    kinds = [e["event"] for e in events]
    assert kinds.count("naca") == 1 and kinds.count("run") == 1 and kinds.count("sweep") == 1
    assert kinds.count("error") == 2 and kinds.count("upload") == 1 and kinds.count("upload_failed") == 1
    errs = [e for e in events if e["event"] == "error"]
    assert all("mesh could not be fitted" in e["message"] for e in errs)
    raw = (tmp_path / "d" / "usage.jsonl").read_text()
    assert "secret-student-name" not in raw and "/home/jo" not in raw and "NACA" not in raw


def test_paths_are_hidden_but_units_are_not(tmp_path):
    log = feedback.UsageLog(tmp_path)
    log.event("error", message=r"Airspeed must be between 1 and 100 m/s; L/D; C:\Users\jo\wing.stl and /home/jo/a.stl.")
    msg = log.read()[0]["message"]
    assert msg == "Airspeed must be between 1 and 100 m/s; L/D; <file> and <file>."


def test_usage_summary_and_export(tmp_path):
    log = feedback.UsageLog(tmp_path)
    for k, extra in (("run", {}), ("run", {}), ("sweep", {}), ("done", {"seconds": 100}), ("done", {"seconds": 300}),
                     ("done", {"seconds": 200}), ("cancel", {}), ("upload", {}),
                     ("error", {"message": "The airflow calculation became unstable, in /tmp/x/y."}),
                     ("bogus", {})):
        log.event(k, **extra)
    s = feedback.usage_summary(log.read())
    assert (s["runs"], s["sweeps"], s["finished"], s["cancels"], s["uploads"], s["errors"]) == (2, 1, 3, 1, 1, 1)
    assert s["median_run_seconds"] == 200
    assert s["error_messages"] == [("The airflow calculation became unstable, in <file>.", 1)]
    feedback.add_feedback(tmp_path, 4, 2, 'Too many "steps", and #hashtags; and commas', "J.D.")
    feedback.add_feedback(tmp_path, None, 5, "", "")
    text = feedback.export_csv(tmp_path, machine=MACHINE)
    check_comment_lines(text)
    head, rows = read_csv_like_pandas(text)
    assert head == feedback.FEEDBACK_COLUMNS
    assert [r[1:5] for r in rows] == [["4", "2", 'Too many "steps", and #hashtags; and commas', "J.D."],
                                      ["", "5", "", ""]]
    assert "# single runs started: 2; sweeps started: 1; finished: 3; cancelled: 1; stopped with a problem: 1" in text
    assert "# median run time: 200 s (3.3 min)" in text
    assert "# problem shown 1x: The airflow calculation became unstable; in <file>." in text
    assert "Ubuntu 24.04 LTS; WSL: yes; 8 logical / 4 physical cores; 15.5 GB RAM" in text
    try:
        import pandas as pd
    except ImportError:
        pd = None
    if pd is not None:
        import io

        df = pd.read_csv(io.StringIO(text), comment="#")
        assert len(df) == 2 and df.iloc[0, 3].endswith("and commas")


def test_cli_feedback_export(tmp_path, capsys):
    data = tmp_path / "d"
    feedback.add_feedback(data, 5, 5, "great", "")
    out = tmp_path / "fb.csv"
    assert cli.main(["feedback", "export", "--data", str(data), "--out", str(out)]) == 0
    assert "Saved" in capsys.readouterr().out
    head, rows = read_csv_like_pandas(out.read_text())
    assert len(rows) == 1 and rows[0][3] == "great"
    assert cli.main(["feedback", "export", "--data", str(data), "--out", "-"]) == 0
    assert capsys.readouterr().out.startswith("# WindLab")
    assert cli.main(["feedback", "export", "--data", str(tmp_path / "nothing-here")]) == 1
    assert "no WindLab data folder" in capsys.readouterr().err
