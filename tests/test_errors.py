"""Every error a student can hit comes back as a plain-language message with a next step:
never a stack trace, an exit code or a bare HTTP status."""

from __future__ import annotations

import errno
import re
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, cube_bytes, naca
from windlab.app import Settings, create_app
from windlab.app import runs as runs_mod
from windlab.runner import RunError

NEXT_STEP = re.compile(r"\b(Try|try|Check|check|Please|please|Delete|delete|Follow|run it again|Run it again|"
                       r"Export|reload|start it again|Close|Fix|Choose|tick|Tick)\b")
JARGON = re.compile(r"Traceback|Exception|exit(ed)? (with )?code|errno|error \d{3}|\(\d{3}\)|HTTP|stderr|"
                    r"Internal Server Error|NoneType|KeyError")


def plain(msg: str) -> str:
    assert msg and isinstance(msg, str)
    assert not JARGON.search(msg), f"jargon in: {msg}"
    assert msg.rstrip().endswith((".", "?", ")")), f"not a sentence: {msg}"
    return msg


def plain_with_next_step(msg: str) -> str:
    plain(msg)
    assert NEXT_STEP.search(msg), f"no next step in: {msg}"
    return msg


def _env(tmp_path, solver=None):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver or FakeSolver()))
    return a, make_client(a)


def _run(c, gid, **kw):
    return c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4, **kw})


# --------------------------------------------------------------------------- solver / mesh failures


@pytest.mark.parametrize("stage, words", [
    ("snappyHexMesh", ["mesh could not be fitted", "one closed solid", "units"]),
    ("checkMesh", ["quality checks", "closed solid"]),
    ("simpleFoam", ["became unstable", "lower airspeed", "smaller angle of attack", "check the part"]),
    ("potentialFoam", ["became unstable", "lower airspeed"]),
    ("blockMesh", ["run it again", "windlab doctor"]),
])
def test_failed_stages_have_plain_messages_with_a_next_step(tmp_path, stage, words):
    a, c = _env(tmp_path, FakeSolver(fail_stage=stage))
    gid = naca(c).json()["geometry_id"]
    rid = _run(c, gid).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "failed" and s["stage_label"] == "Stopped with a problem"
    msg = plain_with_next_step(s["error"])
    for w in words:
        assert w in msg, (w, msg)
    a.fastapi.state.runs.stop()


def test_run_error_messages_directly(tmp_path):
    m = runs_mod._plain_run_error
    plain_with_next_step(m(RunError("setup", "OpenFOAM not found (/usr/lib/openfoam)")))
    assert "install guide" in m(RunError("setup", "x"))
    t = plain_with_next_step(m(RunError("simpleFoam", "timed out")))
    assert "took far longer" in t and "SolidWorks" in t
    for stage in ("surfaceFeatureExtract", "decomposePar", "reconstructPar", "something-new"):
        plain_with_next_step(m(RunError(stage, "exited with code 3\nFOAM FATAL ERROR")))


def test_diverged_sweep_angle_says_what_to_try(tmp_path):
    a, c = _env(tmp_path, FakeSolver(fail_angles=[16]))
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep",
                                    "angles": [0, 16]}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    bad = [p for p in s["points"] if p["status"] == "failed"][0]
    assert "smaller angles" in plain_with_next_step(bad["error"])
    assert any("16°" in w for w in s["warnings"])
    a.fastapi.state.runs.stop()


def test_all_angles_failing_is_a_plain_failure(tmp_path):
    a, c = _env(tmp_path, FakeSolver(fail_angles=[0, 4]))
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep",
                                    "angles": [0, 4]}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    assert plain(c.get(f"/api/runs/{rid}").json()["error"])
    a.fastapi.state.runs.stop()


def test_cancelled_run_is_labelled_plainly(tmp_path):
    a, c = _env(tmp_path, FakeSolver(delay=0.5))
    gid = naca(c).json()["geometry_id"]
    rid = _run(c, gid).json()["run_id"]
    assert c.post(f"/api/runs/{rid}/cancel").status_code == 200
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "cancelled" and s["stage_label"] == "Stopped (you cancelled this run)"
    r = c.post(f"/api/runs/{rid}/cancel")  # asking again is fine
    assert r.status_code == 200
    r = c.get(f"/api/runs/{rid}/results.csv")
    assert r.status_code == 409 and plain(r.json()["detail"])
    a.fastapi.state.runs.stop()


# --------------------------------------------------------------------------- disk full


def test_low_disk_is_refused_before_the_run(tmp_path, monkeypatch):
    a, c = _env(tmp_path)
    gid = naca(c).json()["geometry_id"]
    monkeypatch.setattr(runs_mod, "_free_bytes", lambda path: 120 * 2**20)
    r = _run(c, gid)
    assert r.status_code == 400
    msg = plain_with_next_step(r.json()["detail"])
    assert "disk is almost full" in msg and "120 MB free" in msg and "My runs" in msg
    a.fastapi.state.runs.stop()


class DiskFullSolver(FakeSolver):
    def __call__(self, case_dir, progress=None, cancel=None, on_point=None):
        progress("stage", {"stage": "simpleFoam"})
        raise OSError(errno.ENOSPC, "No space left on device", str(case_dir))


def test_disk_full_during_a_run(tmp_path):
    a, c = _env(tmp_path, DiskFullSolver())
    gid = naca(c).json()["geometry_id"]
    rid = _run(c, gid).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "failed" and "disk is full" in plain_with_next_step(s["error"])
    a.fastapi.state.runs.stop()


def test_unexpected_server_errors_are_plain_json(tmp_path, monkeypatch):
    a, c = _env(tmp_path)
    store = a.fastapi.state.geometry

    def boom(*args, **kw):
        raise RuntimeError("something internal broke: KeyError 'x'")

    monkeypatch.setattr(store, "add_naca", boom)
    r = naca(c)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    msg = plain_with_next_step(r.json()["detail"])
    assert "unexpected problem" in msg and "internal" not in msg

    def full(*args, **kw):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(store, "add_naca", full)
    r = naca(c)
    assert r.status_code == 507 and "disk is full" in plain_with_next_step(r.json()["detail"])
    a.fastapi.state.runs.stop()


# --------------------------------------------------------------------------- inputs and files


@pytest.mark.parametrize("body, words", [
    ({"airspeed": 0.2}, "between 1 and 100 m/s"),
    ({"airspeed": 150}, "between 1 and 100 m/s"),
    ({"alpha_deg": -30}, "between -20 and 20 degrees"),
    ({"alpha_deg": "ten"}, "the angle of attack"),
    ({"airspeed": None}, "the airspeed"),
    ({"kind": "sweep", "angles": [0, 99]}, "between"),
    ({"kind": "sweep", "angles": [5]}, "at least two"),
    ({"kind": "sweep", "angles": ["a", 1]}, "the angles of attack"),
])
def test_out_of_range_inputs(tmp_path, body, words):
    a, c = _env(tmp_path)
    gid = naca(c).json()["geometry_id"]
    r = _run(c, gid, **body)
    assert r.status_code in (400, 422)
    msg = plain(r.json()["detail"])
    assert words in msg and "alpha_deg" not in msg and "body" not in msg
    a.fastapi.state.runs.stop()


@pytest.mark.parametrize("body, words", [
    ({"code": "12"}, "NACA code"), ({"chord": 9}, "chord"), ({"span": "wide"}, "the span"),
])
def test_bad_wing_inputs(tmp_path, body, words):
    a, c = _env(tmp_path)
    r = naca(c, **body)
    assert r.status_code in (400, 422) and words.lower() in plain(r.json()["detail"]).lower()
    a.fastapi.state.runs.stop()


def test_bad_stl_files_say_how_to_fix_them(tmp_path):
    from tests.test_app import CUBE_F

    a, c = _env(tmp_path)
    hdr = {"content-type": "application/octet-stream"}
    r = c.post("/api/geometry", params={"units": "mm"}, content=b"hello, this is not an STL", headers=hdr)
    assert r.status_code == 400 and "Export the part again" in plain_with_next_step(r.json()["detail"])
    r = c.post("/api/geometry", params={"units": "mm"}, content=b"", headers=hdr)
    assert r.status_code == 400 and plain_with_next_step(r.json()["detail"])
    r = c.post("/api/geometry", params={"units": "parsecs"}, content=cube_bytes(tmp_path), headers=hdr)
    assert r.status_code in (400, 422) and plain_with_next_step(r.json()["detail"])
    # an open surface: usable = false with problems that say what to do in SolidWorks
    g = c.post("/api/geometry", params={"units": "mm"}, content=cube_bytes(tmp_path, faces=CUBE_F[:-2]),
               headers=hdr).json()
    assert not g["usable"] and any("SolidWorks" in p for p in g["problems"])
    r = _run(c, g["geometry_id"])
    assert r.status_code == 400 and "problems" in plain_with_next_step(r.json()["detail"])
    a.fastapi.state.runs.stop()


def test_units_mistake_needs_a_confirmation(tmp_path):
    a, c = _env(tmp_path)
    hdr = {"content-type": "application/octet-stream"}
    g = c.post("/api/geometry", params={"units": "m"}, content=cube_bytes(tmp_path, scale=30.0),
               headers=hdr).json()  # a 30 cm part drawn in cm, read as metres
    assert g["size_suspicious"] and g["suggested_units"] == "cm"
    r = _run(c, g["geometry_id"])
    assert r.status_code == 400
    msg = plain_with_next_step(r.json()["detail"])
    assert "30 m long" in msg and "units mistake" in msg and "Yes, this size is right" in msg
    assert _run(c, g["geometry_id"], units_confirmed=True).status_code == 202
    a.fastapi.state.runs.stop()


def test_missing_things_are_plain(tmp_path):
    a, c = _env(tmp_path)
    import uuid

    rid = str(uuid.uuid4())
    for r in (c.get(f"/api/runs/{rid}"), c.get(f"/api/runs/{rid}/slice"), c.get(f"/api/runs/{rid}/surface"),
              c.get(f"/api/runs/{rid}/slice-info"), c.get("/api/runs/not-a-uuid"),
              c.post("/api/runs", json={"geometry_id": rid, "airspeed": 20, "alpha_deg": 0})):
        assert r.status_code in (400, 404) and plain(r.json()["detail"])
    a.fastapi.state.runs.stop()


# --------------------------------------------------------------------------- the page


def test_page_never_shows_http_codes_or_raw_errors():
    js = (Path(__file__).resolve().parents[1] / "windlab" / "app" / "static" / "app.js").read_text()
    assert "error ${r.status}" not in js and "(error " not in js
    assert "server is not answering" in js and "windlab start" in js
    # a failed fetch (server stopped) becomes the plain message, not "TypeError: Failed to fetch"
    api = js[js.index("async function api("):js.index("function richNodes(")]
    assert "catch" in api and "msgNoServer()" in api and "msgUnexpected()" in api
    # the two messages come from the catalog but keep an English fallback for when the catalog itself failed to load
    assert 'hasMessage("msg.noServer") ? t("msg.noServer") : FALLBACK_NO_SERVER' in js
    # losing contact while a run is going is reported after a few failed polls (message in the catalog, ACC-3)
    catalog = (Path(__file__).resolve().parents[1] / "windlab" / "app" / "static" / "i18n" / "en.json").read_text()
    assert 't("run.lostContact", { msg: msgNoServer() })' in js and "Lost contact with WindLab" in catalog
    assert "server is not answering" in catalog and "That file or request is too large" in catalog
