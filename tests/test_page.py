"""The static page: served offline, no external or inline scripts, strict headers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from tests.jscheck import JSCheckError, check_brackets, element_ids
from windlab.app import Settings, create_app


@pytest.fixture
def client(tmp_path):
    return make_client(create_app(Settings(data_dir=tmp_path / "data")))


def test_page_and_static_files(client):
    r = client.get("/")
    assert r.status_code == 200 and "WindLab" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/three/three.module.js").status_code == 200
    assert client.get("/static/../server.py").status_code == 404


def test_results_explain_meaning_and_accuracy(client):
    html = client.get("/").text
    i = html.index("What do these numbers mean")
    block = html[i:html.index("</details>", i)]
    assert "Accuracy" in block and "too high" in block and "turbulent from the leading edge" in block


def test_page_has_no_external_or_inline_scripts(client):
    html = client.get("/").text
    assert "http://" not in html and "https://" not in html
    assert "<script>" not in html  # CSP forbids inline scripts


STATIC = Path(__file__).resolve().parents[1] / "windlab" / "app" / "static"
SCRIPTS = ["app.js", "viewer.js", "chart.js", "plot.js", "preview.js", "section.js", "demo.js"]


def test_3d_views_leave_the_wheel_to_the_page():
    js = (STATIC / "viewer.js").read_text()
    m = re.search(r'addEventListener\("wheel",(.*?)\{ passive: false \}', js, re.S)
    assert m, "wheel handler"
    body = m.group(1)
    # the default (no Ctrl / Cmd, view not focused) returns before preventDefault: the page scrolls
    assert body.index("wheelZooms(") < body.index("return") < body.index("preventDefault")
    assert "e.ctrlKey || e.metaKey || focused" in js and 't("viewer.wheelHint")' in js
    assert "Ctrl + scroll to zoom" in (STATIC / "i18n" / "en.json").read_text()
    css = (STATIC / "app.css").read_text()
    assert re.search(r"\.viewer \{[^}]*touch-action: pan-y", css) and ".viewer.active { touch-action: none; }" in css


def test_colour_maps_and_helpers():
    js = (STATIC / "plot.js").read_text()
    assert not re.search(r"\b(document|window)\.", js)  # pure helpers, no DOM
    viridis = re.search(r"VIRIDIS = \[(.*?)\];", js, re.S).group(1)
    stops = re.findall(r'"(#[0-9a-f]{6})"', viridis)
    assert stops[0] == "#440154" and stops[-1] == "#fde725" and len(stops) == 9  # matplotlib viridis ends
    div = re.findall(r'"(#[0-9a-f]{6})"', re.search(r"DIVERGING = \[(.*?)\];", js, re.S).group(1))
    assert len(div) % 2 == 1 and div[len(div) // 2] == "#f7f7f7"  # neutral exactly in the middle (Cp = 0)
    app = (STATIC / "app.js").read_text()
    assert "slicePositionText(" in app and "toPrecision(3))" not in app.split("slicePositionText")[0][-200:]


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_have_balanced_brackets(name):
    check_brackets((STATIC / name).read_text())


def test_jscheck_catches_slips():
    for bad in ("function f() { if (a) { b(); }", "const x = [1, 2;", "f(`a ${b(}`)", "let s = 'oops;"):
        with pytest.raises(JSCheckError):
            check_brackets(bad)
    check_brackets("const r = /[)}]/g; const t = `x ${a ? `y ${b}` : '}'} z`; // ) comment\n/* ( */ f(1 / 2);")


def test_every_element_the_script_uses_is_on_the_page():
    html = (STATIC / "index.html").read_text()
    ids = set(re.findall(r'\bid="([\w-]+)"', html))
    missing = element_ids((STATIC / "app.js").read_text()) - ids
    assert not missing, f"app.js uses ids that index.html lacks: {sorted(missing)}"
    assert len(ids) == len(re.findall(r'\bid="([\w-]+)"', html)), "duplicate id in index.html"


def test_disclosures_point_at_existing_panels():
    """Every aria-controls / aria-labelledby / aria-describedby names an element on the page."""
    html = (STATIC / "index.html").read_text()
    ids = set(re.findall(r'\bid="([\w-]+)"', html))
    for attr in ("aria-controls", "aria-labelledby", "aria-describedby"):
        for value in re.findall(rf'{attr}="([^"]+)"', html):
            for ref in value.split():
                assert ref in ids, f"{attr} points at missing id {ref!r}"
    # the expected-error ranges (RES-2) have a keyboard-reachable "Why this range?" disclosure
    assert html.count("Why\n              this range?") + html.count("Why this range?") >= 3
    assert 'id="why-cl-btn" aria-expanded="false" aria-controls="why-cl"' in html


def test_upload_form_takes_step_and_stl():
    """GEO-2 on the page: the file input accepts STEP, the units choice has an 'as written in the file' entry
    that app.js shows for STEP files only, and the not-installed note has a home."""
    html = (STATIC / "index.html").read_text()
    assert 'accept=".step,.stp,.stl,model/step,model/stl"' in html
    assert re.search(r'<option value="file" id="units-from-file" hidden[^>]*>As written in the STEP file</option>',
                     html)
    for id_ in ("step-unavailable", "units-from-file-note", "bodies-box", "bodies-toggle"):
        assert f'id="{id_}"' in html, id_
    js = (STATIC / "app.js").read_text()
    assert "function isStepName(name)" in js and 'if (units && units !== "file") q.set("units", units);' in js
    assert "state.config.step_import" in js and "bodies_used" in js
    assert 'data-term="step"' in html  # the glossary explains STEP next to the file input


def test_first_visit_tour_and_start_card():
    """The 'Take the tour' walk-through and the 'Start here' card: dismissible, remembered in localStorage inside
    try/catch, keyboard reachable (buttons, Esc, arrow keys), role=dialog with a labelled heading."""
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    assert 'id="tour-open"' in html and 'aria-controls="tour"' in html
    assert '<section id="tour" class="card tour" role="dialog" aria-labelledby="tour-title"' in html
    for id_ in ("tour-close", "tour-back", "tour-next", "tour-title", "tour-text", "tour-count", "start-card",
                "start-dismiss", "start-lesson", "start-tour"):
        assert f'id="{id_}"' in html, id_
    assert "const TOUR_STEPS = [" in js and js.count("{ target: \"") == 6
    for target in ("step-shape", "form-upload", "step-conditions", "preview-btn", "step-run", "step-results"):
        assert f'target: "{target}"' in js, target
    # remembered, safely
    assert 'try { return localStorage.getItem(key) === "1"; } catch { return false; }' in js
    assert 'try { localStorage.setItem(key, "1"); } catch' in js
    assert 'TOUR_KEY = "windlab-tour-done"' in js and 'START_KEY = "windlab-start-hidden"' in js
    # keyboard: Esc closes, arrows step, focus goes to the heading, the trigger gets focus back
    assert 'if (e.key === "Escape") { e.preventDefault(); closeTour(); }' in js
    assert 'e.key === "ArrowRight"' in js and 'e.key === "ArrowLeft"' in js
    assert '$("tour-title").focus?.({ preventScroll: true })' in js
    assert '(tour.returnTo || $("tour-open")).focus?.()' in js
    # the card suggests the first lesson and is hidden in teacher mode
    assert 'openLesson("lift-vs-angle")' in js and 'document.body.dataset.teacher === "1"' in js
    assert "Lift vs angle of attack" in html
    css = (STATIC / "app.css").read_text()
    assert ".tour-target { outline: 3px dashed var(--focus)" in css


DISCLAIMER = ("This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of the OpenFOAM "
              "software via www.openfoam.com, and owner of the OPENFOAM® and OpenCFD® trade marks.")


def test_trade_mark_notice_and_about_panel(client):
    """PRD 9.3 / task 012 step 4: the footer and the About panel carry OpenCFD's disclaimer and the GPL sentence in
    body-size text; the odd 'developer using OpenFOAM technology' phrase is gone from the product."""
    html = (STATIC / "index.html").read_text()
    flat = " ".join(html.split())
    assert flat.count(DISCLAIMER) == 2  # footer and About panel
    assert flat.count("OpenFOAM is distributed under the GNU GPL v3.") == 2
    assert "OPENFOAM® is a registered trade mark of OpenCFD Limited" in flat
    assert "developer using" not in flat
    for id_ in ("about-open", "about", "about-close", "h-about", "about-version", "about-commit", "about-licences",
                "about-trademark"):
        assert f'id="{id_}"' in html, id_
    assert '<section id="about" class="card about" role="dialog" aria-labelledby="h-about"' in html
    css = (STATIC / "app.css").read_text()
    assert ".foot .trademark, .about .trademark { font-size: 1rem;" in css  # never smaller than body text
    js = (STATIC / "app.js").read_text()
    for must in ("function fillAbout(cfg)", 'setText("about-commit", cfg.commit', "cfg.third_party", "closeAbout()",
                 '$("about").addEventListener("keydown"'):
        assert must in js, must
    cfg = client.get("/api/config").json()
    assert cfg["trademark"]["disclaimer"] == DISCLAIMER and cfg["trademark"]["gpl"].endswith("GNU GPL v3.")
    assert cfg["commit"] and any("OpenFOAM" in x["name"] and "GPL" in x["licence"] for x in cfg["third_party"])
    # nowhere in the shipped product or its documents
    root = Path(__file__).resolve().parents[1]
    hits = [p for p in [*root.glob("windlab/**/*.py"), *root.glob("windlab/**/*.html"), *root.glob("windlab/**/*.js"),
                        *root.glob("windlab/**/*.json"), *root.glob("docs/**/*.md"), root / "README.md"]
            if p.is_file() and "developer using OpenFOAM technology" in p.read_text(errors="replace")]
    assert not hits, hits


def test_survey_matches_the_page():
    """docs/FEEDBACK_SURVEY.md (the student survey) names things that exist on the page and the measures it is for."""
    docs = Path(__file__).resolve().parents[1] / "docs"
    html = (STATIC / "index.html").read_text()
    for button in ("Take the tour", "Make this wing", "Preview the airflow", "Run the wind tunnel", "Why this range?",
                   "Use the pre-computed results", "Check this file", "Open the report", "About WindLab",
                   "Show the numbers as a table"):
        assert button in html or button in (STATIC / "i18n" / "en.json").read_text(), button
    survey = (docs / "FEEDBACK_SURVEY.md").read_text()
    for must in ("SUS", "B1.", "B2.", "B3.", "C1.", "C2.", "windlab feedback export", "likely 0.35 to 0.37",
                 "Unvalidated"):
        assert must in survey, must
    assert 8 <= len(re.findall(r"^\*\*?[ABC]\d\.|^\| [AC]\d\.", survey, re.M)) + survey.count("| A") - 1 <= 20


def test_student_handout_matches_the_current_commands():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "STUDENT_HANDOUT.md").read_text()
    for must in ("wsl --install -d Ubuntu-24.04", "scripts/install_wsl.sh", "windlab start", "http://localhost:8080/",
                 "windlab doctor", "windlab feedback export", "STEP AP214", "STL", "units", "Make this wing",
                 "Run the wind tunnel", "Open the report", "Take the tour", "Uncalibrated prototype",
                 "validation/robustness/parts/", "windlab update", "docs/INSTALL.md", "DISCLAIMER.md"):
        assert must in doc, must
    assert "INSTALL_STUDENT" not in doc and "MONDAY_DEMO" not in doc
    assert doc.count("\n## ") == 4  # one page: four short sections
    assert len(doc.splitlines()) < 90


def test_disclaimer_modal_on_the_page():
    """The educational-use notice: a real modal (role=dialog, aria-modal) with the owner's sentence, a checkbox that
    enables Accept, a link to the full notice, keyboard handling (Tab trapped, Esc ignored), remembered per version
    in localStorage inside try/catch; the Run button and the run request depend on it (windlab/disclaimer.py)."""
    from windlab import disclaimer

    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    flat = " ".join(html.split())
    assert '<div id="disclaimer-modal" class="modal-backdrop" hidden>' in html
    assert ('<section id="disclaimer" class="card modal" role="dialog" aria-modal="true" aria-labelledby="h-disclaimer"'
            in html)
    assert 'aria-describedby="disclaimer-text"' in html and 'id="disclaimer-text"' in html
    assert disclaimer.RESPONSIBILITY in flat  # the owner's sentence, verbatim
    assert disclaimer.ACK_LABEL in flat  # the checkbox text, verbatim
    assert "may be inaccurate" in flat and "not validated for engineering design" in flat
    assert "physical testing" in flat and "qualified engineer" in flat and "MIT License" in flat
    assert '<input type="checkbox" id="disclaimer-ack">' in html
    assert '<button type="button" id="disclaimer-accept" class="primary" disabled' in html
    assert html.count('href="/disclaimer"') == 2  # the modal and the About panel link to the full notice
    assert "DISCLAIMER.md" in flat and "SenSym LLC assumes no responsibility" in flat
    # the About panel's short notice and the footer
    about = html[html.index('id="about"'):html.index('id="lesson-panel"')]
    assert "Educational simulation: please read" in about and 'href="/disclaimer"' in about
    assert "results may be inaccurate (see DISCLAIMER.md)" in " ".join(html[html.index("<footer"):].split())
    # app.js: remembered safely, focus trapped, Esc ignored, the rest of the page inert, header on the run request
    for must in ('DISCLAIMER_KEY = "windlab-disclaimer-accepted"', 'DISCLAIMER_HEADER = "x-windlab-disclaimer"',
                 "try { return localStorage.getItem(DISCLAIMER_KEY); } catch { return null; }",
                 "try { localStorage.setItem(DISCLAIMER_KEY, version); } catch",
                 'if (e.key === "Escape") { e.preventDefault(); return; }', 'if (e.key !== "Tab") return;',
                 'e?.setAttribute("inert", "")', 'e?.removeAttribute("inert")', "function checkDisclaimer()",
                 "storedDisclaimer() === notice.required", 'if (!disclaimerAccepted()) why = t("run.hint.disclaimer")',
                 "[DISCLAIMER_HEADER]: notice.required", "checkDisclaimer();  // the educational-use notice"):
        assert must in js, must
    assert disclaimer.HEADER == "x-windlab-disclaimer"
    css = (STATIC / "app.css").read_text()
    assert ".modal-backdrop { position: fixed; inset: 0; z-index: 20;" in css and ".modal {" in css


def test_imported_names_are_exported():
    for name in SCRIPTS:
        for names, mod in re.findall(r'import\s*\{([^}]*)\}\s*from\s*"\./([\w.]+)"', (STATIC / name).read_text()):
            exported = set(re.findall(r"export\s+(?:async\s+)?(?:class|function|const|let)\s+(\w+)",
                                      (STATIC / mod).read_text()))
            wanted = {n.strip() for n in names.split(",") if n.strip()}
            assert wanted <= exported, f"{name} imports {sorted(wanted - exported)} that {mod} does not export"


def test_page_scripts_are_local_modules(client):
    for name in ("app.js", "viewer.js", "chart.js"):
        js = client.get(f"/static/{name}").text
        assert "http://" not in js and "https://" not in js, name
    assert client.get("/static/vendor/three/three.core.js").status_code == 200
