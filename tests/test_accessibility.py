"""Static accessibility checks of the page (PRD ACC-1/2/4); the rest of the checklist is docs/ACCESSIBILITY.md."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "windlab" / "app" / "static"
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "app.css").read_text()
APP = (STATIC / "app.js").read_text()
VIEWER = (STATIC / "viewer.js").read_text()
CATALOG = (STATIC / "i18n" / "en.json").read_text()  # the page's messages (ACC-3): app.js reaches them via t()

_TAG = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.S)


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'\b{name}="([^"]*)"', attrs)
    return m.group(1) if m else None


def test_every_form_control_has_a_label():
    label_for = set(re.findall(r'<label[^>]*\bfor="([\w-]+)"', HTML))
    wrapped = re.findall(r"<label\b[^>]*>(?:(?!</label>).)*?<(?:input|select)\b[^>]*>", HTML, re.S)
    wrapped_ids = {_attr(w, "id") for w in wrapped}
    for m in _TAG.finditer(HTML):
        attrs = m.group(2)
        if _attr(attrs, "type") in ("hidden",):
            continue
        cid = _attr(attrs, "id")
        before = HTML[max(0, m.start() - 200):m.start()]
        ok = (cid in label_for) or (cid in wrapped_ids) or _attr(attrs, "aria-label") or "<label" in before
        assert ok, f"unlabelled control: <{m.group(1)} {attrs.strip()[:60]}>"


def test_buttons_and_links_have_names():
    for m in re.finditer(r"<(button|a)\b([^>]*)>(.*?)</\1>", HTML, re.S):
        attrs, inner = m.group(2), re.sub(r"<[^>]+>", "", m.group(3)).strip()
        assert inner or _attr(attrs, "aria-label"), f"nameless <{m.group(1)} {attrs.strip()[:60]}>"


def test_no_positive_tabindex_and_pictures_are_focusable_with_instructions():
    assert not re.search(r'tabindex="[1-9]', HTML)
    for vid in ("shape-viewer", "result-viewer", "slice-viewer"):
        tag = re.search(rf'<div id="{vid}"[^>]*>', HTML).group(0)
        assert 'tabindex="0"' in tag, vid
    assert "arrow keys" in re.search(r'<div id="result-viewer"[^>]*>', HTML).group(0).lower()
    assert "Enter reads" in re.search(r'<div id="result-viewer"[^>]*>', HTML).group(0)


def test_no_hover_only_tooltips():
    assert 'title="' not in HTML
    assert ".title =" not in APP and "e.title" not in APP
    # every (?) definition is a real button that opens a note; Esc closes it
    assert re.search(r'<button type="button" class="def"', HTML)
    assert 'e.key === "Escape"' in APP and "aria-expanded" in APP


def test_minimum_target_sizes_in_css():
    assert re.search(r"button\.def, \.def-example \{[^}]*width: 24px;[^}]*height: 24px;", CSS)
    assert re.search(r"\.link-btn \{[^}]*min-height: 24px;[^}]*min-width: 24px;", CSS)
    assert re.search(r"td\.pick input \{[^}]*width: 1\.5rem; height: 1\.5rem;", CSS)
    assert re.search(r"input\[type=radio\], input\[type=checkbox\] \{ width: 1\.25rem; height: 1\.25rem; \}", CSS)
    assert re.search(r"\.lesson-steps button \{[^}]*min-height: 24px;", CSS)


def test_definition_buttons_sit_on_the_label_line():
    """(?) buttons follow the label text on the same line; the control starts a new line (browser review)."""
    assert re.search(r"\.field label \{[^}]*display: inline-block;", CSS)
    assert re.search(r"\.field > input, \.field > select, \.field > textarea \{ display: block; \}", CSS)
    # and the label/for pairing is kept: the button is a sibling after the label, never inside it
    for m in re.finditer(r'<label for="([\w-]+)">[^<]*</label>\s*<button type="button" class="def"', HTML):
        assert m.group(1) in HTML


def test_visible_focus_and_reduced_motion():
    assert ":focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }" in CSS
    assert re.search(r"@media \(prefers-reduced-motion: reduce\) \{.*?transition: none;.*?scroll-behavior: auto", CSS,
                     re.S)
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in APP
    assert 'scrollIntoView({ behavior: "smooth"' not in APP  # every scroll goes through scrollTo()
    assert "function scrollTo(el)" in APP


def test_status_is_never_colour_alone():
    assert 't("run.stage.state", { state: t(`run.stage.${state}`) })' in APP  # stage list
    assert '"run.stage.finished": "finished"' in CATALOG
    assert "⚠ unvalidated" in CATALOG and 't("res.range.unvalidated")' in APP  # expected-error ranges
    assert '(fine ? "✓ " : "⚠ ")' in APP  # comparison notes
    assert '.msgs.problems li::marker { content: "✕  "; }' in CSS
    assert '.msgs.warnings li::marker { content: "⚠  "; }' in CSS
    assert '"✓ Correct."' in CATALOG and "✗ Not quite" in CATALOG and 't("lesson.correct")' in APP  # lesson questions


def test_live_regions_and_probe_readouts():
    for rid in ("stage-label", "slice-probe", "surface-probe", "lesson-note", "glossary-count", "fb-note"):
        tag = re.search(rf'<p id="{rid}"[^>]*>', HTML).group(0)
        assert "aria-live" in tag or 'role="status"' in tag, rid
    for rid in ("run-error", "shape-error", "lesson-error", "compare-error"):
        assert 'role="alert"' in re.search(rf'<p id="{rid}"[^>]*>', HTML).group(0), rid
    # the probes: click and keyboard, on both pictures
    assert "probeAtPixel(" in VIEWER and 'e.key === "Home"' in VIEWER and 'k === "Enter" || k === "p"' in VIEWER
    assert "sliceViewer.onProbe" in APP and "resultViewer.onProbe" in APP
    assert "pressure coefficient Cp" in CATALOG and 't("res.probe.surface.value"' in APP


def test_charts_and_tables_carry_the_same_numbers():
    for cid in ("chart-cl-alpha", "chart-polar", "chart-ld", "cmp-cl", "chart-cl", "chart-cd"):
        tag = re.search(rf'<canvas id="{cid}"[^>]*>', HTML).group(0)
        assert 'tabindex="0"' in tag and 'role="img"' in tag and "aria-label" in tag, cid
    assert 'id="sweep-table"' in HTML and 'id="hist-table"' in HTML and 'id="compare-table"' in HTML
    assert '$("chart-cl-alpha").setAttribute("aria-label", t("sweep.aria.cl", { summary,' in APP
    assert '"sweep.aria.cl": "Lift coefficient against angle of attack. {summary}.{stall}"' in CATALOG


def test_checklist_document_exists_and_matches_reality():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "ACCESSIBILITY.md").read_text()
    for must in ("prefers-reduced-motion", "24", "axe-core", "Keyboard-only paths", "tests/test_accessibility.py",
                 "Still to do by hand"):
        assert must in doc, must
