"""Angle-of-attack sweeps: case building, API, summary (max L/D, stall), CSV downloads."""

from __future__ import annotations

import csv
import io
import json

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from windlab.app import Settings, create_app
from windlab.app.runs import sweep_summary
from windlab.case_builder import (
    FlowConditions,
    GeometryInput,
    InputError,
    ReferenceValues,
    build_case,
    build_sweep_case,
    freestream_directions,
    sweep_angles,
)
from windlab.geometry.naca import naca_wing


def _app(tmp_path, solver=None):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver or FakeSolver()))
    return a, make_client(a)


def _sweep(c, gid, angles=(-4, 4, 12), **kw):
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep", "angles": list(angles), **kw})
    assert r.status_code == 202, r.text
    return r.json()


@pytest.fixture
def wing_geom(tmp_path):
    w = naca_wing("0012", chord=0.3, span=0.6)
    p = tmp_path / "w.stl"
    w.write_stl(p)
    return GeometryInput(stl_path=p, bounds_min=tuple(w.vertices.min(0)), bounds_max=tuple(w.vertices.max(0)))


REF = ReferenceValues(area=0.18, length=0.3)


# --------------------------------------------------------------------------- case builder


def test_sweep_angles_validation():
    assert sweep_angles([12, -4, 4, 4.0]) == [-4.0, 4.0, 12.0]
    for bad, word in (([5], "at least two"), ([0] * 1 + list(range(1, 9)), "at most 8"), ([0, 25], "between"),
                      ([0, "x"], "number"), ([0, float("nan")], "between")):
        with pytest.raises(InputError, match=word):
            sweep_angles(bad)


def test_sweep_case_renders_each_angle_without_remeshing(wing_geom, tmp_path):
    case = build_sweep_case(wing_geom, 20.0, [-4, 0, 8], REF, n_procs=2, runs_root=tmp_path / "runs")
    meta = json.loads((case.path / "windlab_case.json").read_text())
    assert meta["sweep"]["angles"] == [-4.0, 0.0, 8.0] and case.params["angles"] == [-4.0, 0.0, 8.0]
    single = build_case(wing_geom, FlowConditions(airspeed=20, alpha_deg=8), REF, n_procs=2,
                        runs_root=tmp_path / "single")
    # one mesh for all angles: the mesh dictionaries don't depend on the angle
    for f in ("system/blockMeshDict", "system/snappyHexMeshDict"):
        assert (case.path / f).read_text() == (single.path / f).read_text()
    # the angle-dependent files of the 8 deg point are exactly those of a single 8 deg case
    p2 = case.path / "sweep" / "p2"
    assert (p2 / "U").read_text() == (single.path / "0.orig" / "U").read_text()
    assert (p2 / "controlDict").read_text() == (single.path / "system" / "controlDict").read_text()
    drag, lift = freestream_directions(-4)
    assert meta["sweep"]["points"][0]["lift_dir"] == pytest.approx(lift.tolist())
    assert "#codeStream" not in (p2 / "controlDict").read_text()


# --------------------------------------------------------------------------- summary


def _pt(a, cl, cd=0.05, status="done"):
    return {"alpha_deg": a, "cl": cl, "cd": cd, "l_over_d": cl / cd, "status": status}


def test_summary_max_l_over_d_and_stall():
    pts = [_pt(-4, -0.3), _pt(0, 0.0, 0.02), _pt(4, 0.3, 0.025), _pt(8, 0.6, 0.04), _pt(12, 0.8, 0.07),
           _pt(16, 0.7, 0.15)]
    s = sweep_summary(pts)
    assert s["max_l_over_d_alpha"] == 8 and s["max_l_over_d"] == pytest.approx(15.0)
    assert s["cl_max"] == 0.8 and s["cl_max_alpha"] == 12 and s["stall_alpha"] == 12
    assert "RANS stall predictions are uncertain" in s["stall_note"] and "12°" in s["stall_note"]


def test_summary_no_stall_and_failed_points_ignored():
    s = sweep_summary([_pt(0, 0.0), _pt(4, 0.3), _pt(8, 0.6), {"alpha_deg": 12, "status": "failed"}])
    assert s["stall_alpha"] is None and s["stall_note"] is None and s["cl_max_alpha"] == 8
    assert sweep_summary([])["max_l_over_d"] is None


# --------------------------------------------------------------------------- API


def test_sweep_lifecycle(tmp_path):
    a, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    run = _sweep(c, gid, (-4, 0, 4, 8, 12, 16))
    assert run["params"]["kind"] == "sweep" and run["params"]["alpha_deg"] is None
    assert [p["status"] for p in run["points"]] == ["waiting"] * 6 or run["status"] != "queued"
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{run['run_id']}").json()
    assert s["status"] == "done", s
    pts = s["result"]["points"]
    assert [p["alpha_deg"] for p in pts] == [-4, 0, 4, 8, 12, 16]
    assert all(p["status"] == "done" for p in pts)
    assert pts[3]["cl"] == pytest.approx(0.6) and pts[3]["cm"] == pytest.approx(-0.01)
    assert s["result"]["stall_alpha"] == 12 and "RANS" in s["result"]["stall_note"]
    assert s["result"]["max_l_over_d_alpha"] in (4, 8)
    assert any("16°" in w for w in s["warnings"])  # 16 deg did not settle
    assert s["surface_points"] == list(range(6))
    # per-angle pictures and histories
    assert c.get(f"/api/runs/{run['run_id']}/surface", params={"point": 2}).status_code == 200
    assert c.get(f"/api/runs/{run['run_id']}/surface").status_code == 200  # default: the best-L/D angle
    h = c.get(f"/api/runs/{run['run_id']}", params={"point": 1}).json()
    assert h["history"][-1][0] == 320 and h["history_point"] == 1
    assert c.get(f"/api/runs/{run['run_id']}", params={"point": 6}).status_code == 400
    # in the run list
    r = c.get("/api/runs").json()["runs"][0]
    assert r["kind"] == "sweep" and r["angles"] == [-4, 0, 4, 8, 12, 16]
    a.fastapi.state.runs.stop()


def test_progress_never_jumps_to_the_end_between_angles(tmp_path):
    """Regression: between two angles the stage used to stay "surface" with the next index."""
    import threading
    import time

    a, c = _app(tmp_path, FakeSolver(delay=0.15))
    gid = naca(c).json()["geometry_id"]
    rid = _sweep(c, gid, (-4, 4, 12))["run_id"]
    seen, stop = [], threading.Event()

    def poll():
        while not stop.is_set():
            s = c.get(f"/api/runs/{rid}").json()
            if s["status"] == "running":
                i = s["point_index"]
                seen.append((i, s["percent"], s["stage"], s["points"][i]["status"]))
            time.sleep(0.005)

    t = threading.Thread(target=poll)
    t.start()
    assert a.fastapi.state.runs.wait_idle(20)
    stop.set()
    t.join()
    # once a new angle has started, its stage is never "surface" before its solver has run
    for idx in (1, 2):
        stages = [stage for i, _, stage, _ in seen if i == idx]
        first_solve = stages.index("simpleFoam") if "simpleFoam" in stages else len(stages)
        assert "surface" not in stages[:first_solve], (idx, stages[:first_solve])
    last_angle_solving = [pct for i, pct, stage, _ in seen if i == 2 and stage != "surface"]
    assert last_angle_solving and max(last_angle_solving) < 95
    a.fastapi.state.runs.stop()


def test_sweep_survives_a_failed_angle(tmp_path):
    a, c = _app(tmp_path, FakeSolver(fail_angles={12}))
    gid = naca(c).json()["geometry_id"]
    rid = _sweep(c, gid)["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "done"
    st = {p["alpha_deg"]: p["status"] for p in s["result"]["points"]}
    assert st == {-4: "done", 4: "done", 12: "failed"}
    assert any("No result at 12°" in w for w in s["warnings"])
    a.fastapi.state.runs.stop()


def test_sweep_all_angles_failed(tmp_path):
    a, c = _app(tmp_path, FakeSolver(fail_angles={-4, 4}))
    gid = naca(c).json()["geometry_id"]
    rid = _sweep(c, gid, (-4, 4))["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "failed" and "any of the angles" in s["error"]
    a.fastapi.state.runs.stop()


def test_sweep_cancel_marks_remaining_angles(tmp_path):
    a, c = _app(tmp_path, FakeSolver(delay=0.3))
    gid = naca(c).json()["geometry_id"]
    rid = _sweep(c, gid, (-4, 4, 12))["run_id"]
    import time

    end = time.monotonic() + 10
    while time.monotonic() < end:
        s = c.get(f"/api/runs/{rid}").json()
        if s["status"] == "running" and s["points"][0]["status"] == "done":
            break
        time.sleep(0.02)
    assert "angle 2 of 3" in s["stage_label"] or s["stage"] != "simpleFoam"
    c.post(f"/api/runs/{rid}/cancel")
    assert a.fastapi.state.runs.wait_idle(10)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "cancelled"
    assert s["points"][0]["status"] == "done" and s["points"][-1]["status"] == "cancelled"
    a.fastapi.state.runs.stop()


@pytest.mark.parametrize("body, word", [
    ({"angles": [5]}, "at least two"),
    ({"angles": list(range(9))}, "at most 8"),
    ({"angles": [0, 30]}, "between -20 and 20"),
    ({"angles": None}, "angles"),
    ({"angles": ["a", 1]}, "angles"),
    ({"angles": list(range(25))}, "angles"),
    ({"airspeed": 500}, "between 1 and 100 m/s"),
])
def test_sweep_rejects_bad_input(tmp_path, body, word):
    _, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep", "angles": [0, 4], **body})
    assert r.status_code in (400, 422), r.text
    assert word in r.json()["detail"]


def test_single_run_needs_an_angle(tmp_path):
    _, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20})
    assert r.status_code == 400 and "angle of attack" in r.json()["detail"]
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0, "kind": "polar"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- CSV


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_sweep_csv(tmp_path):
    a, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    rid = _sweep(c, gid, (-4, 4, 12))["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    r = c.get(f"/api/runs/{rid}/results.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and rid[:8] in r.headers["content-disposition"]
    rows = _rows(r.text)
    assert rows[0][0].startswith("# WindLab") and "not yet validated" in rows[0][0]
    head = next(row for row in rows if not row[0].startswith("#"))
    assert head[:5] == ["angle of attack [deg]", "airspeed [m/s]", "Reynolds number [-]", "CL [-]", "CD [-]"]
    assert "lift [N]" in head and "drag [N]" in head
    data = rows[rows.index(head) + 1:]
    assert [float(x[0]) for x in data] == [-4, 4, 12]
    cl, cd, lift = float(data[1][3]), float(data[1][4]), float(data[1][7])
    assert cl == pytest.approx(0.3) and lift == pytest.approx(0.5 * 1.225 * 20**2 * 0.18 * cl, rel=1e-3)
    assert cd > 0 and data[1][head.index("status")] == "done"
    r.text.encode("ascii")  # plain ASCII: opens cleanly in any spreadsheet
    h = c.get(f"/api/runs/{rid}/history.csv", params={"point": 2})
    hrows = _rows(h.text)
    assert ["iteration [-]", "CL [-]", "CD [-]"] in hrows and any("12" in row[0] for row in hrows if row[0][0] == "#")
    a.fastapi.state.runs.stop()


def test_single_run_csv(tmp_path):
    a, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 5}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    rows = _rows(c.get(f"/api/runs/{rid}/results.csv").text)
    data = rows[-1]
    status = rows[-2].index("status")
    assert float(data[0]) == 5 and float(data[3]) == pytest.approx(0.45) and data[status] == "done"
    hist = _rows(c.get(f"/api/runs/{rid}/history.csv").text)
    assert hist[-1][0] == "300"
    assert c.get(f"/api/runs/{rid}/history.csv", params={"point": 0}).status_code == 400  # not a sweep
    a.fastapi.state.runs.stop()


def read_csv_like_pandas(text: str) -> tuple[list[str], list[list[str]]]:
    """What pandas.read_csv(io.StringIO(text), comment='#') sees: everything from a '#'
    outside quotes to the end of the line is dropped, empty lines are skipped, the first
    remaining row is the header. Asserts the table is rectangular."""
    kept = []
    for line in text.splitlines():
        quoted, cut = False, len(line)
        for i, ch in enumerate(line):
            if ch == '"':
                quoted = not quoted
            elif ch == "#" and not quoted:
                cut = i
                break
        if line[:cut].strip():
            kept.append(line[:cut])
    rows = list(csv.reader(kept))
    head, data = rows[0], rows[1:]
    assert all(len(r) == len(head) for r in data), "ragged table"
    return head, data


def check_comment_lines(text: str) -> None:
    """Comment lines: start with '# ', one cell in Excel (no commas), no quotes, one '#'."""
    for line in text.splitlines():
        if line.startswith("#") or line.startswith('"#'):
            assert line.startswith("# ") and "," not in line and '"' not in line and line.count("#") == 1, line


def test_csv_files_open_cleanly_in_pandas_and_excel(tmp_path):
    from windlab.app.runs import csv_comment

    assert csv_comment('wing "A", v2 #3\nnext') == "# wing 'A'; v2 no. 3 next\n"
    a, c = _app(tmp_path)
    gid = naca(c).json()["geometry_id"]
    sid = _sweep(c, gid, (-4, 4))["run_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 5}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    texts = {
        "sweep results": c.get(f"/api/runs/{sid}/results.csv").text,
        "sweep history": c.get(f"/api/runs/{sid}/history.csv", params={"point": 1}).text,
        "single results": c.get(f"/api/runs/{rid}/results.csv").text,
        "single history": c.get(f"/api/runs/{rid}/history.csv").text,
    }
    for name, text in texts.items():
        check_comment_lines(text)
        head, data = read_csv_like_pandas(text)
        assert head[0] in ("angle of attack [deg]", "iteration [-]") and data, name
    head, data = read_csv_like_pandas(texts["sweep results"])
    assert len(data) == 2 and head[3] == "CL [-]"
    try:
        import pandas as pd
    except ImportError:
        pd = None
    if pd is not None:  # the real thing, where it is installed
        for text in texts.values():
            df = pd.read_csv(io.StringIO(text), comment="#")
            assert df.shape[0] >= 1 and not df.columns[0].startswith("#")
    a.fastapi.state.runs.stop()


def test_csv_before_the_run_finished(tmp_path):
    a, c = _app(tmp_path, FakeSolver(delay=0.3))
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 5}).json()["run_id"]
    assert c.get(f"/api/runs/{rid}/results.csv").status_code == 409
    a.fastapi.state.runs.wait_idle(10)
    a.fastapi.state.runs.stop()
