"""The static GitHub Pages demo (scripts/build_static_demo.py + windlab/app/static/demo.js)."""

from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

import pytest

from tests.jscheck import check_brackets
from windlab import lessons
from windlab.app import security

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_static_demo as demo  # noqa: E402

STATIC = ROOT / "windlab" / "app" / "static"
APP = (STATIC / "app.js").read_text()
DEMO_JS = (STATIC / "demo.js").read_text()


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo") / "site"
    manifest = demo.build(out)
    return out, manifest


def test_site_is_small_complete_and_self_contained(site):
    out, m = site
    assert m["total_bytes"] < demo.SIZE_LIMIT and m["runs"] >= 13
    for rel in m["files"]:
        assert (out / rel).is_file(), rel
    html = (out / "index.html").read_text()
    assert '<html lang="en" data-demo="1">' in html
    assert 'http-equiv="Content-Security-Policy"' in html and "connect-src 'self'" in html  # SEC-10
    # frame-ancestors is ignored (with a console warning) in a <meta> policy: header-only, so the real app
    # keeps it in its HTTP header and the demo's meta policy is that header minus this one directive
    assert "frame-ancestors" not in html
    assert "frame-ancestors 'none'" in security.CSP
    assert security.CSP == demo.CSP + "; frame-ancestors 'none'"
    assert re.search(r'<script type="module" src="\./static/app\.js" integrity="sha384-[A-Za-z0-9+/=]+">', html)
    assert re.search(r'href="\./static/app\.css" integrity="sha384-', html)
    assert not re.search(r'=["\'](/static/|/api/)', html) and "localhost" not in html
    assert "Limited online demo: this is not the WindLab simulator." in html
    assert "demo-badge" in html and "demo-strip" in html and "demo-notice" in html
    assert "Nothing you type here is sent or uploaded anywhere" in html
    assert "how to install" in html and demo.REPO_URL in html
    # the educational-use notice: the same modal as the installed app, its links pointing at the bundled full text
    from windlab import disclaimer
    assert 'id="disclaimer-modal"' in html and 'id="disclaimer-accept"' in html and 'id="disclaimer-ack"' in html
    assert disclaimer.ACK_LABEL in " ".join(html.split()) and disclaimer.RESPONSIBILITY in " ".join(html.split())
    assert 'href="/disclaimer"' not in html and html.count(f'href="./{demo.DISCLAIMER_FILE}"') == 2
    assert (out / demo.DISCLAIMER_FILE).read_text(encoding="utf-8") == disclaimer.FULL_TEXT
    assert demo.DISCLAIMER_FILE in m["files"]
    # the same front end: every script and the vendored three.js, with its licence
    for name in [*demo.SCRIPTS, "app.css", "vendor/three/three.module.js", "vendor/three/LICENSE"]:
        assert (out / "static" / name).read_bytes() == (STATIC / name).read_bytes(), name


def test_every_lesson_run_and_shape_is_bundled_under_the_ids_demo_js_uses(site):
    out, _ = site
    for card in lessons.list_lessons():
        lesson = json.loads((out / "data" / "lessons" / f"{card['id']}.json").read_text())
        assert lesson["answers"] == {} and lesson["id"] == card["id"]
        for step in lesson["steps"]:
            if step["kind"] != "run":
                continue
            d = out / "data" / "runs" / f"{card['id']}--{step['pack']}"
            status = json.loads((d / "status.json").read_text())
            assert status["run_id"] == d.name and status["status"] == "done" and status["result"]
            assert status["surface_available"] and status["slice_available"]
            assert (d / "summary.json").is_file() and (d / "results.csv").is_file()
            points = range(len(status["points"])) if status["params"]["kind"] == "sweep" else [None]
            for i in points:
                s = "" if i is None else f"_{i}"
                for f in (f"surface{s}.bin.gz", f"slice{s}.bin.gz", f"sliceinfo{s}.json", f"history{s}.csv"):
                    assert (d / f).is_file(), (d.name, f)
            assert (out / "data" / "geometry" / demo.geometry_key(step["shape"]) / "preview.bin.gz").is_file()
    cfg = json.loads((out / "data" / "config.json").read_text())
    assert cfg["lesson"] is None and cfg["builtin_shapes"] and cfg["naca"]["chord"]
    assert json.loads((out / "data" / "lessons.json").read_text())["teacher_mode"] is False


def test_demo_js_mirrors_the_servers_ids_and_routes():
    check_brackets(DEMO_JS)
    assert "http://" not in DEMO_JS and "https://" not in DEMO_JS and 'const DATA = "./data";' in DEMO_JS
    # the geometry keys demo.js builds equal the build script's
    assert "`naca-${code}-${fmt(chord)}-${fmt(span)}`" in DEMO_JS
    assert demo.geometry_key({"naca": {"code": "0012", "chord": 0.3, "span": 0.6}}) == "naca-0012-0.3-0.6"
    assert demo.geometry_key({"builtin": {"shape": "teardrop", "diameter": 0.2, "fineness": 3}}) == \
        "teardrop-diameter_0.2-fineness_3"
    for route in ("/api/config", "/api/glossary", "/api/lessons", "/api/runs", "/api/compare", "/api/geometry/naca",
                  "/api/geometry/builtin"):
        assert route in DEMO_JS, route
    assert "DecompressionStream" in DEMO_JS and "localStorage" in DEMO_JS
    assert "Install WindLab to use it" in DEMO_JS
    # a lesson's "Open all N pre-computed results and compare them" goes through the import route for every
    # run step and then Compare with all of them: the demo's import adds each to the opened list and its
    # Compare letters go to E (five bodies in Streamlining), like the server's
    assert "/import$/)) && method === \"POST\"" in DEMO_JS and "store(STORE_RUNS, [id, ...ids])" in DEMO_JS
    assert '"ABCDE"[index]' in DEMO_JS and '"ABCDE"[index]' in (ROOT / "windlab" / "app" / "runs.py").read_text()
    assert "async function useAllPackRuns(runSteps, step, button)" in APP
    assert "if (compared) await showCompare(ids);" in APP


def test_app_js_has_the_demo_hooks():
    assert 'const DEMO = document.documentElement.dataset.demo === "1";' in APP
    assert 'import("./demo.js")' in APP and "function fileUrl(path)" in APP
    assert 'fileUrl(`/api/runs/${s.run_id}/results.csv`)' in APP
    # the Run button's note in the demo comes from the message catalog (ACC-3)
    catalog = (STATIC / "i18n" / "en.json").read_text()
    assert 't("run.hint.demo")' in APP and "runs only in the installed WindLab" in catalog
    assert 't("demo.uploadOff")' in APP and "install WindLab to upload your own part" in catalog
    assert "i18n.js" in demo.SCRIPTS and "i18n/en.json" in demo.VENDOR  # shipped with the static site


def test_served_site_has_no_404s(site):
    """Serve site/ with python -m http.server on 8081 and fetch every referenced file (then stop it)."""
    out, _ = site
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 8081)) == 0:
            pytest.skip("port 8081 is in use")
    assert demo.check(out, 8081) == 0
