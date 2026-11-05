"""Guided lessons (EDU-1/EDU-2): content, packs, import into My runs, local answers, teacher mode, docs."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver
from tests.test_sweep import read_csv_like_pandas
from windlab import cli, feedback, lessons
from windlab.app import Settings, create_app, surface
from windlab.app import slice as flow_slice

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "windlab" / "app" / "static" / "app.js").read_text()
EXPECTED_IDS = ["lift-vs-angle", "reynolds-and-scale", "streamlining", "aspect-ratio", "why-simulations-disagree"]


# --------------------------------------------------------------------------- content


def test_five_lessons_each_fit_a_class():
    items = lessons.list_lessons()
    assert [i["id"] for i in items] == EXPECTED_IDS
    for item in items:
        lesson = lessons.get_lesson(item["id"])
        assert lesson["minutes"] == 50 and 2 <= len(lesson["objectives"]) <= 3
        assert sum(t["minutes"] for t in lesson["timeline"]) == 50
        kinds = [s["kind"] for s in lesson["steps"]]
        assert kinds[0] == "intro" and "predict" in kinds[: kinds.index("run")]
        assert kinds.count("question") == 3 and kinds[-1] == "homework"
        assert len(lesson.get("misconceptions", [])) >= 3
        assert lesson["markdown"].startswith("# ")
        for term in lesson["glossary"]:
            assert term in {t["id"] for t in __import__("windlab.glossary", fromlist=["terms"]).terms()}, term
        for s in lesson["steps"]:
            if s["kind"] == "run":
                assert s["pack_available"], f"{item['id']}/{s['pack']} has no pack"
                assert "shape" in s and "conditions" in s


def test_check_lesson_rejects_bad_content(tmp_path):
    good = lessons.get_lesson("lift-vs-angle")
    lessons.check_lesson(good)
    bad = json.loads(json.dumps(good))
    bad["steps"] = [s for s in bad["steps"] if s["kind"] != "predict"]
    with pytest.raises(ValueError, match="predicts before"):
        lessons.check_lesson(bad)
    bad = json.loads(json.dumps(good))
    bad["steps"][0]["id"] = bad["steps"][1]["id"]
    with pytest.raises(ValueError, match="unique"):
        lessons.check_lesson(bad)
    bad = json.loads(json.dumps(good))
    next(s for s in bad["steps"] if s["kind"] == "question")["answer"] = 9
    with pytest.raises(ValueError, match="index of the right one"):
        lessons.check_lesson(bad)
    with pytest.raises(lessons.LessonError):
        lessons.parse_id("../etc")
    with pytest.raises(KeyError):
        lessons.get_lesson("no-such-lesson")


def test_packs_are_compact_and_complete():
    total = 0
    for item in lessons.list_lessons():
        lesson = lessons.get_lesson(item["id"])
        for s in lesson["steps"]:
            if s["kind"] != "run":
                continue
            run = lessons.load_pack(item["id"], s["pack"])
            total += run["size_bytes"]
            assert run["pack_version"] == lessons.PACK_VERSION and run["result"]["converged"] in (True, False)
            assert run["params"]["preset"] == "quick" and run["computed"].startswith("2026-")
            sweep = run["params"]["kind"] == "sweep"
            n = len(run["result"]["points"]) if sweep else 1
            for i in range(n):
                suffix = f"_{i}" if sweep else ""
                assert f"surface{suffix}.bin.gz" in run["files"] and f"slice{suffix}.bin.gz" in run["files"]
            assert "history.json" in run["files"]
            # the pack's inputs agree with the lesson step
            c = s["conditions"]
            assert run["params"]["airspeed"] == c["airspeed"]
            if sweep:
                assert [pt["alpha_deg"] for pt in run["result"]["points"]] == [float(a) for a in c["angles"]]
            else:
                assert run["params"]["alpha_deg"] == c["alpha_deg"]
            assert run["size_bytes"] < 2.5 * 2**20
    assert total < 8 * 2**20  # every pack together


def test_pack_from_run_round_trip(tmp_path):
    """Make a fake finished run, pack it, and check the pack reads back."""
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    gid = c.post("/api/geometry/naca", json={"code": "0012", "chord": 0.3, "span": 0.6}).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 5}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    st = a.fastapi.state.runs.get(rid)
    out = tmp_path / "pack" / "test"
    size = lessons.pack_from_run(st.case_dir, out, "test")
    assert size > 0 and (out / "run.json").is_file() and (out / "surface.bin.gz").is_file()
    run = json.loads((out / "run.json").read_text())
    assert run["result"]["cl"] == pytest.approx(0.45) and run["params"]["alpha_deg"] == 5
    hist = json.loads((out / "history.json").read_text())
    assert hist["0"][-1][0] == 300
    a.fastapi.state.runs.stop()


# --------------------------------------------------------------------------- API


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c, tmp_path / "d"
    a.fastapi.state.runs.stop()


def test_lessons_api_lists_and_serves(env):
    _, c, _ = env
    d = c.get("/api/lessons").json()
    assert [x["id"] for x in d["lessons"]] == EXPECTED_IDS and d["teacher_mode"] is False
    lesson = c.get("/api/lessons/lift-vs-angle").json()
    assert lesson["title"] == "Lift vs angle of attack" and lesson["answers"] == {}
    assert c.get("/api/lessons/nope").status_code == 404
    assert c.get("/api/lessons/..%2F..").status_code in (400, 404)


def test_import_pack_run_behaves_like_a_finished_run(env):
    a, c, _ = env
    r = c.post("/api/lessons/streamlining/import", json={"run": "sphere"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["status"] == "done" and s["params"]["source"] == "lesson pack"
    assert s["params"]["pack"] == "streamlining/sphere"
    assert s["params"]["geometry_class"] == "bluff-smooth" and s["params"]["geometry_name"].startswith("Sphere")
    assert s["result"]["cd"] == pytest.approx(0.252, abs=0.002)
    assert s["result"]["expected"]["cd"]["status"] == "validated"  # a swinging lift is allowed for this class
    assert any("Pre-computed result" in w for w in s["warnings"])
    assert s["surface_available"] and s["slice_available"] and s["history"][-1][0] == s["result"]["iterations"]
    rid = s["run_id"]
    # the pictures decode, the CSV and the OpenFOAM case export work, it is listed under My runs
    assert len(surface.decode(c.get(f"/api/runs/{rid}/surface").content)["triangles"]) > 1000
    assert flow_slice.decode(c.get(f"/api/runs/{rid}/slice").content)["speed_max"] > 1.0
    assert c.get(f"/api/runs/{rid}/slice-info").json()["streamlines"]
    assert "CD [-]" in c.get(f"/api/runs/{rid}/results.csv").text
    assert c.get(f"/api/runs/{rid}/case.zip").status_code == 200
    listed = [x for x in c.get("/api/runs").json()["runs"] if x["run_id"] == rid]
    assert listed and listed[0]["source"] == "lesson pack"
    # importing again returns the same run
    assert c.post("/api/lessons/streamlining/import", json={"run": "sphere"}).json()["run_id"] == rid
    # a sweep pack: per-angle pictures and histories
    s2 = c.post("/api/lessons/lift-vs-angle/import", json={"run": "naca0012-sweep"}).json()
    assert s2["status"] == "done" and len(s2["result"]["points"]) == 6 and s2["surface_points"] == list(range(6))
    assert s2["result"]["points"][3]["cl"] == pytest.approx(0.36, abs=0.005)
    assert c.get(f"/api/runs/{s2['run_id']}/history.csv", params={"point": 5}).status_code == 200
    # the two imported runs compare like any others
    assert c.get("/api/compare", params={"runs": f"{rid},{s2['run_id']}"}).status_code == 200
    # unknown pack / step
    assert c.post("/api/lessons/streamlining/import", json={"run": "nope"}).status_code == 404
    assert c.post("/api/lessons/streamlining/import", json={"run": "../x"}).status_code == 422


def test_a_multi_run_lesson_imports_every_pack_and_compares_them_all(env):
    """The page's "Open all N pre-computed results and compare them" (app.js useAllPackRuns, in the app
    and in the demo alike) imports each run step's pack through POST /api/lessons/{id}/import and then
    asks GET /api/compare for all of them: the Streamlining lesson's five bodies side by side."""
    _, c, _ = env
    lesson = c.get("/api/lessons/streamlining").json()
    steps = [s for s in lesson["steps"] if s["kind"] == "run"]
    assert [s["pack"] for s in steps] == ["flat-plate", "cube", "sphere", "cylinder", "teardrop"]
    ids = []
    for s in steps:
        r = c.post("/api/lessons/streamlining/import", json={"run": s["pack"]})
        assert r.status_code == 201 and r.json()["status"] == "done", (s["pack"], r.text)
        ids.append(r.json()["run_id"])
    assert len(set(ids)) == 5
    listed = {x["run_id"] for x in c.get("/api/runs").json()["runs"]}
    assert set(ids) <= listed
    r = c.get("/api/compare", params={"runs": ",".join(ids)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["letter"] for x in d["runs"]] == ["A", "B", "C", "D", "E"]
    names = [x["name"] for x in d["runs"]]
    assert names[0].startswith("Flat plate") and names[1].startswith("Cube") and names[4].startswith("Streamlined")
    cds = [x["points"][0]["cd"] for x in d["runs"]]
    assert cds[0] > 1 and cds[1] > 1 and cds[4] < 0.1  # "from a drag coefficient above 1 to below 0.1"
    # plate 0.04 m², sphere 0.031 m²: each body on its own frontal area is the textbook comparison
    assert any("its own frontal area" in w and "compared directly" in w for w in d["warnings"])
    assert c.get("/api/compare.csv", params={"runs": ",".join(ids)}).status_code == 200
    # the page offers the button only for lessons with several runs, and compares when they fit
    assert "useAllPackRuns(runSteps, step, all)" in APP_JS and "ids.push(await importPackRun(s));" in APP_JS
    assert "const compared = ids.length >= 2 && ids.length <= MAX_COMPARE;" in APP_JS
    assert "const MAX_COMPARE = 5;" in APP_JS
    for card in lessons.list_lessons():
        n = len(card["runs"])
        assert n <= 5, (card["id"], n)  # every lesson's runs fit one comparison


def test_answers_are_saved_locally_and_graded(env):
    _, c, data = env
    r = c.post("/api/lessons/lift-vs-angle/answers", json={"step": "predict-curve", "answer": "CL rises\nthen stalls"})
    assert r.status_code == 201 and r.json()["correct"] is None
    r = c.post("/api/lessons/lift-vs-angle/answers", json={"step": "q1", "answer": 1})
    assert r.status_code == 201 and r.json()["correct"] is True and "0.045" in r.json()["explanation"]
    r = c.post("/api/lessons/lift-vs-angle/answers", json={"step": "q2", "answer": 0})
    assert r.json()["correct"] is False and r.json()["answer"] == 1
    assert c.post("/api/lessons/lift-vs-angle/answers", json={"step": "q1", "answer": 7}).status_code == 400
    assert c.post("/api/lessons/lift-vs-angle/answers", json={"step": "q1", "answer": "b"}).status_code == 400
    assert c.post("/api/lessons/lift-vs-angle/answers", json={"step": "nope", "answer": 1}).status_code == 404
    latest = c.get("/api/lessons/lift-vs-angle/answers").json()["answers"]
    assert latest["predict-curve"]["answer"] == "CL rises then stalls" and latest["q2"]["correct"] is False
    assert c.get("/api/lessons/lift-vs-angle").json()["answers"]["q1"]["correct"] is True
    raw = (data / "lesson_answers.jsonl").read_text()
    assert raw.count("\n") == 3
    # ... and they go into the feedback export
    text = feedback.export_csv(data, machine={"os": "x"})
    head, rows = read_csv_like_pandas(text)
    assert "lesson answer" in head
    kinds = [r[head.index("kind")] for r in rows]
    assert kinds == ["lesson answer"] * 3
    assert rows[1][head.index("answer correct")] == "yes" and rows[2][head.index("answer correct")] == "no"
    assert "lesson answers: 3 (comprehension questions right: 1 of 2)" in text


def test_teacher_mode(tmp_path, monkeypatch, capsys):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver(), lesson="streamlining"))
    c = make_client(a)
    assert c.get("/api/config").json()["lesson"] == "streamlining"
    d = c.get("/api/lessons").json()
    assert d["teacher_mode"] is True and d["lesson"] == "streamlining"
    a.fastapi.state.runs.stop()
    with pytest.raises(ValueError, match="unknown lesson"):
        create_app(Settings(data_dir=tmp_path / "e", lesson="nope"))
    # the CLI flag and the lessons listing
    monkeypatch.setenv("WINDLAB_DATA", str(tmp_path / "data"))
    args = cli.build_parser().parse_args(["start", "--lesson", "aspect-ratio"])
    seen = {}
    assert cli.start(args, serve=lambda app, host, port: seen.update(app=app)) == 0
    assert "teacher mode" in capsys.readouterr().out and seen["app"].fastapi.state.settings.lesson == "aspect-ratio"
    args = cli.build_parser().parse_args(["start", "--lesson", "nope"])
    assert cli.start(args, serve=lambda *a: None) == 2
    assert "unknown lesson" in capsys.readouterr().err
    assert cli.main(["lessons"]) == 0
    out = capsys.readouterr().out
    assert "lift-vs-angle" in out and "pre-computed results included" in out


# --------------------------------------------------------------------------- docs


def test_worksheets_and_instructor_guides_are_rendered_from_the_lessons():
    sys.path.insert(0, str(ROOT / "scripts"))
    import render_lessons

    rendered = render_lessons.render_all()
    for name, text in rendered.items():
        on_disk = ROOT / "docs" / "lessons" / name
        assert on_disk.is_file(), f"run  python scripts/render_lessons.py  ({name} missing)"
        assert on_disk.read_text() == text, f"run  python scripts/render_lessons.py  ({name} out of date)"
    ws = rendered["lift-vs-angle-worksheet.md"]
    assert "Write each prediction before you run" in ws and "- [ ] about 0.045 per degree" in ws
    assert "(correct)" not in ws  # the worksheet never gives the answers away
    ig = rendered["lift-vs-angle-instructor.md"]
    assert "## Minute by minute" in ws or "## Minute by minute" in ig
    assert "| 0-5 |" in ig and "**about 0.045 per degree (correct)**" in ig and "## Common misconceptions" in ig
    assert re.search(r"\| 8 \| 0\.36\d \| 0\.04\d+ \|", ig)  # the pre-computed numbers are in the guide


def test_page_has_the_lessons_ui():
    html = (ROOT / "windlab" / "app" / "static" / "index.html").read_text()
    for must in ('id="lessons"', 'id="lesson-panel"', 'id="lesson-step"', 'id="lesson-next"', 'id="lesson-back"',
                 'href="#lessons"', 'class="advanced"'):
        assert must in html, must
    js = (ROOT / "windlab" / "app" / "static" / "app.js").read_text()
    catalog = (ROOT / "windlab" / "app" / "static" / "i18n" / "en.json").read_text()
    assert "Use the pre-computed results" in catalog and "Set up this run for me" in catalog and "/api/lessons/" in js
    assert '"lesson.run.usePack"' in js and 't("lesson.run.setup")' in js
    assert 'data-teacher="1"' in (ROOT / "windlab" / "app" / "static" / "app.css").read_text()
