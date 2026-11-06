#!/usr/bin/env python3
"""Build the static GitHub Pages demo of WindLab: the same front end in "demo mode".

    python scripts/build_static_demo.py [--out site] [--check [--port 8081]]

The demo reads the bundled lesson result packs, the glossary and the lessons from static files
instead of the API (windlab/app/static/demo.js), disables uploading and live solving with a
friendly note, and includes the instant 2D preview. It never contacts a server (a meta
Content-Security-Policy, Subresource Integrity on the page's own files, no backend URL). The
educational-use notice (the modal of windlab/disclaimer.py) is shown exactly as in the installed app.

What it writes under ``site/``::

    index.html                       the page with data-demo="1", relative URLs, CSP and SRI
    disclaimer.txt                   the full notice (DISCLAIMER.md), linked from the page
    static/*.js, app.css, vendor/    the front end, unchanged
    data/config.json, glossary.json, validation_table.json, lessons.json, lessons/<id>.json
    data/runs/<lesson>--<pack>/      status.json, summary.json, results.csv, history[_i].csv,
                                     surface[_i].bin.gz, slice[_i].bin.gz, sliceinfo[_i].json
    data/geometry/<key>/preview.bin.gz   3D view of each lesson shape (keyed like demo.js keys them)
    manifest.json                    every file with its size (the --check option fetches each one)

``--check`` serves ``site/`` with ``python -m http.server`` on the given port, fetches every file
in the manifest plus everything index.html references, reports any non-200 answer, and stops the
server. The run statuses are produced by the real app (``RunManager.import_pack``) in a temporary
data folder, so they carry the same expected-error ranges and warnings as the installed WindLab.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from windlab import __version__, disclaimer, expected_error, glossary, lessons  # noqa: E402
from windlab.app import Settings, create_app, surface  # noqa: E402
from windlab.app.runs import run_summary  # noqa: E402
from windlab.app.server import STATIC  # noqa: E402

SCRIPTS = ["app.js", "chart.js", "plot.js", "viewer.js", "preview.js", "section.js", "demo.js", "i18n.js"]
VENDOR = ["vendor/three/three.module.js", "vendor/three/three.core.js", "vendor/three/LICENSE",
          "vendor/three/README.md", "i18n/en.json"]
SIZE_LIMIT = 15 * 2**20
REPO_URL = "https://github.com/isac-artzi/SenSym-Windtunnel"
# The real app's policy (windlab/app/security.py) minus frame-ancestors: browsers ignore that directive in a
# <meta> element and log a warning; it only works as an HTTP header, which a static site cannot send.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'")
DEMO_BANNER = (
    '<div class="banner demo" role="note"><p><strong>Limited online demo: this is not the WindLab simulator.</strong> '
    "This page cannot run a simulation. The Run button is switched off and you cannot upload your own part.</p>"
    "<p><strong>What works here:</strong> the instant airflow preview (a rough 2D sketch drawn in your browser, not a "
    "simulation result), the glossary, the tour, and the five lessons with results the WindLab team computed "
    "in advance with the full app. Every number on this page comes from those pre-computed runs "
    "or from the rough preview.</p>"
    "<p><strong>To simulate your own shapes</strong>, install the free WindLab on your own computer "
    f'(<a href="{REPO_URL}#readme">how to install</a>). Nothing you type here is sent or uploaded anywhere.</p></div>'
)
DEMO_BADGE = '<span class="demo-badge">Limited online demo</span>'
DEMO_STRIP = (
    '<div class="demo-strip" role="note"><strong>Limited online demo</strong>: no simulations, no uploads. '
    f'<a href="{REPO_URL}#readme">Install WindLab</a> for the full simulator.</div>'
)
DEMO_MODAL_NOTE = (
    '<p class="demo-notice"><strong>You are opening the limited online demo, not the full WindLab.</strong> It cannot '
    "run simulations or take your own parts; it shows the instant preview and results computed in advance. The full "
    "WindLab runs on your own computer.</p>"
)
DISCLAIMER_FILE = "disclaimer.txt"  # the full notice, next to index.html (the app serves it at /disclaimer)


def fmt_g(x: float) -> str:
    """Python's :g, which matches JavaScript's String(Number(x)) for the sizes lessons use."""
    return f"{float(x):g}"


def geometry_key(shape: dict) -> str:
    """The id demo.js gives a shape made from its formula (must match demo.js: nacaView / builtinView)."""
    if "naca" in shape:
        n = shape["naca"]
        return f"naca-{n['code']}-{fmt_g(n['chord'])}-{fmt_g(n['span'])}"
    b = dict(shape["builtin"])
    kind = b.pop("shape")
    from windlab.geometry.shapes import SHAPES
    order = [p["id"] for p in SHAPES[kind]["params"]]
    return f"{kind}-" + "-".join(f"{k}_{fmt_g(b[k])}" for k in order)


def sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def demo_index(html: str, sri_css: str, sri_js: str) -> str:
    """The page for the demo: relative URLs, demo flag, CSP, SRI, the demo banner."""
    out = html
    subs = [
        ('<html lang="en">', '<html lang="en" data-demo="1">'),
        ('<link rel="stylesheet" href="/static/app.css">',
         f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
         f'  <link rel="stylesheet" href="./static/app.css" integrity="{sri_css}">'),
        ('<script type="module" src="/static/app.js"></script>',
         f'<script type="module" src="./static/app.js" integrity="{sri_js}"></script>'),
    ]
    for old, new in subs:
        if out.count(old) != 1:
            raise SystemExit(f"index.html changed: expected exactly one {old!r}")
        out = out.replace(old, new)
    # the educational-use notice: the modal stays as it is; its "full notice" links point at the bundled file
    if 'id="disclaimer-modal"' not in out or 'id="disclaimer-accept"' not in out:
        raise SystemExit("index.html changed: the educational-use notice modal is missing")
    if out.count('href="/disclaimer"') < 1:
        raise SystemExit("index.html changed: no link to /disclaimer")
    out = out.replace('href="/disclaimer"', f'href="./{DISCLAIMER_FILE}"')
    # the title keeps its data-i18n attribute (the catalog fills it); the demo adds its own suffix after that
    title = re.compile(r"(<title[^>]*>)WindLab — virtual wind tunnel(</title>)")
    if len(title.findall(out)) != 1:
        raise SystemExit("index.html changed: the <title> moved")
    out = title.sub(r"\1WindLab — limited online demo (not the full simulator)\2", out)
    anchor = "Use the numbers to learn and compare, not to design real hardware.</p>"
    if out.count(anchor) != 1:
        raise SystemExit("index.html changed: the prototype banner moved")
    out = out.replace(anchor, anchor + "\n    " + DEMO_BANNER)
    h1 = re.compile(r'(<h1>WindLab <span class="sub"[^>]*>[^<]*</span>)(</h1>)')
    if len(h1.findall(out)) != 1:
        raise SystemExit("index.html changed: the page heading moved")
    out = h1.sub(lambda m: m.group(1) + " " + DEMO_BADGE + m.group(2), out)
    if out.count('<div id="disclaimer-text">') != 1:
        raise SystemExit("index.html changed: the disclaimer text moved")
    out = out.replace('<div id="disclaimer-text">', '<div id="disclaimer-text">\n        ' + DEMO_MODAL_NOTE)
    if out.count("</body>") != 1:
        raise SystemExit("index.html changed: no single </body>")
    out = out.replace("</body>", "  " + DEMO_STRIP + "\n</body>")
    if re.search(r'=["\'](/static/|/api/)', out) or "localhost" in out:
        raise SystemExit("the demo page still has an absolute /static/ or /api/ URL, or names localhost")
    return out


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("src", "href") and v and not v.startswith(("#", "http", "mailto:")):
                self.refs.append(v)


def _gz(path: Path, data: bytes) -> None:
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(data)


def build(out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    (out / "data").mkdir()
    css = (STATIC / "app.css").read_bytes()
    js = (STATIC / "app.js").read_bytes()
    (out / "index.html").write_text(demo_index((STATIC / "index.html").read_text(), sri(css), sri(js)))
    (out / DISCLAIMER_FILE).write_text(disclaimer.FULL_TEXT, encoding="utf-8")
    for name in ["app.css", *SCRIPTS, *VENDOR]:
        dst = out / "static" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STATIC / name, dst)

    with tempfile.TemporaryDirectory(prefix="windlab-demo-") as tmp:
        app = create_app(Settings(data_dir=Path(tmp), n_procs=1))
        runs, geometry = app.fastapi.state.runs, app.fastapi.state.geometry
        try:
            from tests.asgi_client import make_client
            client = make_client(app)
            config = client.get("/api/config").json()
            config["lesson"] = None
            _json(out / "data" / "config.json", config)
            _json(out / "data" / "glossary.json", glossary.load())
            _json(out / "data" / "validation_table.json", expected_error.load_table())
            _json(out / "data" / "lessons.json", {"lessons": lessons.list_lessons(), "teacher_mode": False,
                                                  "lesson": None})
            (out / "data" / "lessons").mkdir()
            (out / "data" / "runs").mkdir()
            (out / "data" / "geometry").mkdir()
            shapes_done = set()
            n_runs = 0
            for card in lessons.list_lessons():
                lesson = lessons.get_lesson(card["id"])
                lesson["answers"] = {}
                for step in lesson["steps"]:
                    if step["kind"] == "run":
                        step["pack_available"] = True
                _json(out / "data" / "lessons" / f"{card['id']}.json", lesson)
                for step in lesson["steps"]:
                    if step["kind"] != "run" or not step.get("pack"):
                        continue
                    rid = f"{card['id']}--{step['pack']}"
                    st = runs.import_pack(card["id"], step["pack"], step)
                    _export_run(runs, st, out / "data" / "runs" / rid, rid)
                    n_runs += 1
                    key = geometry_key(step["shape"])
                    if key not in shapes_done:
                        shapes_done.add(key)
                        d = out / "data" / "geometry" / key
                        d.mkdir()
                        blob = surface.stl_surface(geometry.stl_path(st.geometry_id))
                        sd = surface.decode(blob)
                        v, t, _ = surface.decimate(sd["vertices"], sd["triangles"], None, lessons.PACK_MAX_TRIANGLES)
                        _gz(d / "preview.bin.gz", surface.encode(v, t, None))
        finally:
            runs.stop(timeout=1.0)

    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "manifest.json")
    manifest = {"version": __version__, "built": time.strftime("%Y-%m-%d"), "runs": n_runs,
                "files": {str(p.relative_to(out)).replace("\\", "/"): p.stat().st_size for p in files}}
    manifest["total_bytes"] = sum(manifest["files"].values())
    _json(out / "manifest.json", manifest)
    return manifest


def _json(path: Path, data) -> None:
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))


def _export_run(runs, st, d: Path, rid: str) -> None:
    """One imported pack run as the files demo.js reads (ids replaced by the stable slug)."""
    d.mkdir()
    status = runs.status(st.run_id)
    status["run_id"] = rid
    status.pop("queue_position", None)
    status["queue_position"] = 0
    _json(d / "status.json", status)
    summary = run_summary(st)
    summary["run_id"] = rid
    _json(d / "summary.json", summary)
    _, text = runs.results_csv(st.run_id)
    (d / "results.csv").write_text(text.replace(st.run_id, rid))
    points = list(range(len(st.points))) if st.params.get("kind") == "sweep" else [None]
    for i in points:
        suffix = "" if i is None else f"_{i}"
        with contextlib.suppress(Exception):  # a pack without a history for this angle
            _, text = runs.history_csv(st.run_id, i)
            (d / f"history{suffix}.csv").write_text(text.replace(st.run_id, rid))
        for what in ("surface", "slice"):
            with contextlib.suppress(KeyError):  # a pack without this picture
                _gz(d / f"{what}{suffix}.bin.gz", runs.surface_blob(st.run_id, i, what=what))
        with contextlib.suppress(KeyError, OSError, ValueError, surface.SurfaceError):
            _json(d / f"sliceinfo{suffix}.json", runs.slice_info(st.run_id, i))


# ---------------------------------------------------------------- the 404 check


def check(out: Path, port: int) -> int:
    manifest = json.loads((out / "manifest.json").read_text())
    refs = _Refs()
    refs.feed((out / "index.html").read_text())
    wanted = {re.sub(r"^\./", "", r) for r in refs.refs} | set(manifest["files"]) | {"manifest.json"}
    for name in SCRIPTS:  # modules imported by the scripts (import "./x.js")
        for mod in re.findall(r'from\s+"\./([\w.]+)"', (out / "static" / name).read_text()):
            wanted.add(f"static/{mod}")
        for mod in re.findall(r'import\("\./([\w.]+)"\)', (out / "static" / name).read_text()):
            wanted.add(f"static/{mod}")
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory",
                               str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bad = []
    try:
        base = f"http://127.0.0.1:{port}/"
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "manifest.json", timeout=1).read()
                break
            except Exception:  # noqa: BLE001 - the server is still starting
                time.sleep(0.1)
        for rel in sorted(wanted):
            try:
                with urllib.request.urlopen(base + rel, timeout=10) as r:
                    if r.status != 200:
                        bad.append((rel, r.status))
            except Exception as e:  # noqa: BLE001
                bad.append((rel, getattr(e, "code", str(e))))
    finally:
        server.terminate()
        server.wait(timeout=10)
    print(f"checked {len(wanted)} files on port {port}: {len(bad)} problems")
    for rel, why in bad:
        print(f"  {why}: {rel}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "site")
    ap.add_argument("--check", action="store_true", help="serve the result and fetch every file (no 404s)")
    ap.add_argument("--port", type=int, default=8081)
    args = ap.parse_args()
    m = build(args.out)
    mb = m["total_bytes"] / 2**20
    print(f"built {args.out}: {len(m['files'])} files, {mb:.1f} MB, {m['runs']} pre-computed runs")
    html = (args.out / "index.html").read_text()
    modal = ('id="disclaimer-modal"' in html and 'id="disclaimer-accept"' in html
             and (args.out / DISCLAIMER_FILE).is_file())
    print(f"educational-use notice modal: {'present' if modal else 'MISSING'}")
    if not modal:
        return 1
    if m["total_bytes"] > SIZE_LIMIT:
        print(f"warning: larger than the {SIZE_LIMIT / 2**20:.0f} MB target")
    return check(args.out, args.port) if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
