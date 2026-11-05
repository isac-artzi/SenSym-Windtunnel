"""JavaScript safety net (task 012, step 1): the page scripts are parsed and run by Node.

1. ``node --input-type=module --check`` on every .js file under windlab/app/static (and on the built
   site/ copies when the static demo has been built): a SyntaxError such as a duplicate ``const``
   fails the test instead of silently stopping the whole page script in the browser.
2. A headless smoke test (tests/js/smoke.mjs on the tiny DOM in tests/js/dom_shim.mjs): every module
   evaluates, app.js starts up against fixtures made by the real FastAPI app (config, glossary,
   lessons, a NACA geometry, three finished runs) and wires its handlers; the "Make this wing" flow
   reaches the Run button; the tour, radios, glossary buttons and theme selector react to events; the
   My runs labels and Compare (task 014). Limits are listed at the top of smoke.mjs and dom_shim.mjs:
   no layout, no rendering, no WebGL, no browser.
3. Unit tests of the pure helpers in plot.js (tests/js/plot_helpers.mjs): coincident markers and the
   keyboard stepping of the charts, ticks.

Both skip with a clear reason when ``node`` is not installed (the static checks in tests/test_page.py
and tests/test_i18n.py still run).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from windlab.app import Settings, create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "windlab" / "app" / "static"
SITE_STATIC = ROOT / "site" / "static"
JS_DIR = Path(__file__).resolve().parent / "js"
NODE = shutil.which("node")
NO_NODE = "node is not installed in this environment (the scripts are then only checked statically)"

pytestmark = pytest.mark.skipif(NODE is None, reason=NO_NODE)


def _node(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, *args], capture_output=True, text=True, timeout=120, **kw)


def _stderr(r: subprocess.CompletedProcess) -> str:
    return "\n".join(ln for ln in r.stderr.splitlines() if "ExperimentalWarning" not in ln
                     and "--trace-warnings" not in ln)


def js_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.js") if p.is_file())


# --------------------------------------------------------------------------- 1. syntax


@pytest.mark.parametrize("path", js_files(STATIC), ids=lambda p: str(p.relative_to(STATIC)))
def test_every_script_parses_as_an_es_module(path):
    with path.open() as fh:
        r = _node(["--input-type=module", "--check"], stdin=fh)
    assert r.returncode == 0, f"{path.relative_to(ROOT)} does not parse:\n{_stderr(r)}"


def test_built_site_scripts_parse():
    if not SITE_STATIC.is_dir():
        pytest.skip("site/ is not built (python scripts/build_static_demo.py)")
    files = js_files(SITE_STATIC)
    assert files, "site/static has no scripts"
    for path in files:
        with path.open() as fh:
            r = _node(["--input-type=module", "--check"], stdin=fh)
        assert r.returncode == 0, f"{path.relative_to(ROOT)} does not parse:\n{_stderr(r)}"


# --------------------------------------------------------------------------- 2. smoke

VOID = {"input", "meta", "link", "br", "hr", "img", "source", "wbr", "col"}


class _Tree(HTMLParser):
    """index.html as nested dicts {"t": tag, "a": {attrs}, "c": [children or text]} for dom_shim.mjs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = None
        self.stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        node = {"t": tag, "a": {k: ("" if v is None else v) for k, v in attrs}, "c": []}
        if self.stack:
            self.stack[-1]["c"].append(node)
        else:
            self.root = node
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack:
            top = self.stack.pop()
            if top["t"] == tag:
                break

    def handle_data(self, data):
        if self.stack and data:
            self.stack[-1]["c"].append(data)


def html_tree(html: str) -> dict:
    p = _Tree()
    p.feed(html)
    p.close()
    assert p.root and p.root["t"] == "html"
    return p.root


def write_fixtures(data_dir: Path, app_dir: Path) -> dict:
    """dom.json and routes.json: the page and the API answers it will get, from the real app."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dom.json").write_text(json.dumps(html_tree((STATIC / "index.html").read_text())))
    app = create_app(Settings(data_dir=app_dir, n_procs=2, runner=FakeSolver()))
    try:
        c = make_client(app)
        config = c.get("/api/config").json()
        wing = naca(c).json()
        gid = wing["geometry_id"]
        # three finished runs of the wing for the My runs table and Compare: 5° and 8° at 20 m/s, and a sweep
        # (task 014: their labels must differ by speed and angle, not only by the date)
        ids = [c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": a}).json()["run_id"]
               for a in (5, 8)]
        ids.append(c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep",
                                             "angles": [-4, 0, 4]}).json()["run_id"])
        assert app.fastapi.state.runs.wait_idle(60)
        routes = {
            "GET /api/config": {"json": config},
            "GET /api/glossary": {"json": c.get("/api/glossary").json()},
            "GET /api/lessons": {"json": c.get("/api/lessons").json()},
            "GET /api/runs": {"json": c.get("/api/runs").json()},
            "GET /api/compare": {"json": c.get("/api/compare", params={"runs": ",".join(ids[:2])}).json()},
            "GET /api/estimate": {"json": c.get("/api/estimate", params={"geometry_id": gid}).json()},
            "POST /api/geometry/naca": {"json": wing},
            "POST /api/geometry/builtin": {"json": c.post("/api/geometry/builtin",
                                                          json={"shape": "sphere", "diameter": 0.2}).json()},
            f"GET /api/geometry/{gid}": {"json": wing},
            "GET /api/geometry/*/preview": {"status": 404, "json": {"detail": "no preview in the smoke test"}},
            "GET /api/geometry/*/section": {"json": c.get(f"/api/geometry/{gid}/section").json()},
            "GET /api/lessons/*": {"json": c.get("/api/lessons/lift-vs-angle").json()},
        }
    finally:
        app.fastapi.state.runs.stop()
    (data_dir / "routes.json").write_text(json.dumps(routes))
    return routes


def run_smoke(work: Path, catalog: Path | None = None) -> dict:
    """Copy the static folder (as an ES-module package), write the fixtures and run smoke.mjs."""
    static_copy = work / "static"
    if not static_copy.exists():
        shutil.copytree(STATIC, static_copy)
        (static_copy / "package.json").write_text('{"type": "module"}\n')
        write_fixtures(work / "data", work / "app")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "WINDLAB_SMOKE_STATIC": static_copy.resolve().as_uri() + "/",
           "WINDLAB_SMOKE_DATA": str(work / "data"), "NODE_NO_WARNINGS": "1"}
    if catalog is not None:
        env["WINDLAB_SMOKE_CATALOG"] = str(catalog)
    r = _node([str(JS_DIR / "smoke.mjs")], env=env, cwd=str(work))
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"smoke.mjs produced no report (exit {r.returncode}):\n{r.stdout}\n{_stderr(r)}"
    report = json.loads(lines[-1])
    report["exit"] = r.returncode
    report["stderr"] = _stderr(r)
    return report


def failures(report: dict) -> str:
    bad = [f"module {m['name']}: {m.get('error')}" for m in report["modules"] if not m["ok"]]
    bad += [f"check '{c['name']}': {c['detail']}" for c in report["checks"] if not c["ok"]]
    bad += [f"error: {e}" for e in report["errors"]]
    return "\n".join(bad)


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    return run_smoke(tmp_path_factory.mktemp("jssmoke"))


@pytest.fixture(scope="module")
def pseudo_smoke(tmp_path_factory):
    """The same page with a pseudo-localised catalog (every message ⟦wrapped⟧ and 30 % longer, ACC-3)."""
    from tests.test_i18n import MESSAGES, pseudo_localise

    work = tmp_path_factory.mktemp("jspseudo")
    catalog = work / "pseudo.json"
    catalog.write_text(json.dumps({"locale": "en-x-pseudo", "messages": pseudo_localise(MESSAGES)}, ensure_ascii=False))
    return run_smoke(work, catalog=catalog)


def test_every_module_evaluates(smoke):
    names = {m["name"] for m in smoke["modules"]}
    assert {"app.js", "chart.js", "plot.js", "viewer.js", "preview.js", "section.js", "demo.js", "i18n.js",
            "vendor/three/three.module.js"} <= names
    bad = [m for m in smoke["modules"] if not m["ok"]]
    assert not bad, "\n".join(f"{m['name']}: {m['error']}" for m in bad)


def test_app_starts_and_wires_its_handlers(smoke):
    checks = {c["name"]: c for c in smoke["checks"]}
    for name in ("no uncaught error during start-up", "loads the message catalog", "version is shown from /api/config",
                 "#run-btn listens for click", "#form-naca listens for submit", "#form-upload listens for submit",
                 "#preview-btn listens for click", "#tour-open listens for click", "#compare-btn listens for click",
                 "every input[name=shape-kind] listens for change", "glossary buttons were enhanced"):
        assert name in checks, f"smoke.mjs no longer reports '{name}'"
        assert checks[name]["ok"], f"{name}: {checks[name]['detail']}"


def test_make_this_wing_reaches_the_run_button(smoke):
    checks = {c["name"]: c for c in smoke["checks"]}
    for name in ("Make this wing posts /api/geometry/naca", "shape report is shown with a headline",
                 "Step 2 is enabled", "Reynolds number is shown", "Run button is enabled",
                 "airspeed out of range shows an error", "tour closes after the last step",
                 "glossary button opens a definition note", "theme selector sets data-theme"):
        assert checks[name]["ok"], f"{name}: {checks[name]['detail']}"


def test_every_smoke_check_passes(smoke):
    """Everything smoke.mjs checks, so a new check there needs no new test here."""
    assert smoke["ok"] and smoke["exit"] == 0, failures(smoke) + "\n" + smoke["stderr"]
    assert not smoke["errors"]
    assert len(smoke["checks"]) >= 70 and len(smoke["modules"]) == 9
    unrouted = [f["url"] for f in smoke["fetches"] if f.get("unrouted")]
    assert not unrouted, f"the page called routes the smoke fixtures do not answer: {unrouted}"


def test_plot_helpers_in_node(tmp_path):
    """tests/js/plot_helpers.mjs: the pure helpers of plot.js (coincident markers, keyboard stepping, ticks)."""
    static_copy = tmp_path / "static"
    shutil.copytree(STATIC, static_copy)
    (static_copy / "package.json").write_text('{"type": "module"}\n')
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "WINDLAB_SMOKE_STATIC": static_copy.resolve().as_uri() + "/",
           "NODE_NO_WARNINGS": "1"}
    r = _node([str(JS_DIR / "plot_helpers.mjs")], env=env, cwd=str(tmp_path))
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"plot_helpers.mjs produced no report (exit {r.returncode}):\n{r.stdout}\n{_stderr(r)}"
    report = json.loads(lines[-1])
    bad = [f"{c['name']}: {c.get('detail')}" for c in report["checks"] if not c["ok"]]
    assert report["ok"] and r.returncode == 0 and not bad, "\n".join(bad) + "\n" + _stderr(r)
    assert len(report["checks"]) >= 10


def test_pseudo_localised_page_shows_only_catalog_text(pseudo_smoke):
    """With every message ⟦wrapped⟧, every data-i18n element, every data-i18n-attr attribute and every text the
    scripts wrote during the flow carry the pseudo text: nothing user-visible is left hard-coded in English."""
    assert pseudo_smoke["pseudo"]
    checks = {c["name"]: c for c in pseudo_smoke["checks"]}
    for name in ("every data-i18n element shows the pseudo-localised text",
                 "every data-i18n-attr attribute is pseudo-localised", "messages app.js wrote are pseudo-localised",
                 "headline comes from the catalog"):
        assert name in checks and checks[name]["ok"], f"{name}: {checks.get(name, {}).get('detail')}"
    assert pseudo_smoke["ok"], failures(pseudo_smoke)
