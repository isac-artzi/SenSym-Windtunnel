"""Glossary (PRD UX-1): one JSON source, served by the API, rendered to docs, used by every (?) on the page."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from windlab import glossary
from windlab.app import Settings, create_app

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "windlab" / "app" / "static" / "index.html").read_text()

# terms the PRD lists for the default path; each must be defined and reachable from the page
DEFAULT_PATH_TERMS = ["lift-coefficient", "drag-coefficient", "angle-of-attack", "reynolds-number", "chord", "span",
                      "aspect-ratio", "reference-area", "pressure-coefficient", "lift-to-drag-ratio", "drag-polar",
                      "stall", "boundary-layer", "mesh", "y-plus", "streamline", "convergence"]


def test_glossary_is_well_formed_and_first_year_level():
    g = glossary.load()
    ids = {t["id"] for t in g["terms"]}
    assert set(DEFAULT_PATH_TERMS) <= ids
    for t in g["terms"]:
        assert 20 <= len(t["short"]) <= 260, t["id"]  # one plain sentence
        assert t["short"].rstrip().endswith((".", ")")), t["id"]
        assert "shall" not in t["short"]  # no spec-speak
    glossary.check(g)  # consistent cross-references


def test_check_rejects_broken_entries():
    with pytest.raises(ValueError, match="unknown"):
        glossary.check({"terms": [{"id": "a", "term": "A", "short": "x.", "see": ["nope"]}]})
    with pytest.raises(ValueError, match="duplicate"):
        glossary.check({"terms": [{"id": "a", "term": "A", "short": "x."}, {"id": "a", "term": "B", "short": "y."}]})
    with pytest.raises(ValueError, match="bad glossary id"):
        glossary.check({"terms": [{"id": "Not Ok", "term": "A", "short": "x."}]})


def test_every_definition_button_on_the_page_has_a_term():
    buttons = re.findall(r'<button type="button" class="def"[^>]*>', HTML, re.S)
    used = [re.search(r'data-term="([\w-]+)"', b).group(1) for b in buttons]
    ids = {t["id"] for t in glossary.terms()}
    missing = sorted(set(used) - ids)
    assert not missing, f"index.html uses glossary ids that do not exist: {missing}"
    for term in DEFAULT_PATH_TERMS:
        assert term in used, f"no (?) button for {term} on the default path"
    # every (?) is a real button with a label (keyboard reachable, named for screen readers)
    for b in buttons:
        assert 'aria-label="' in b, b
    assert len(buttons) >= 30
    assert 'id="glossary"' in HTML and 'id="glossary-filter"' in HTML


def test_docs_glossary_is_rendered_from_the_json():
    rendered = glossary.markdown()
    on_disk = (ROOT / "docs" / "GLOSSARY.md").read_text()
    assert on_disk == rendered, "run  python scripts/render_glossary.py"
    assert "## Reynolds number" in rendered and "## y⁺ (y-plus)" in rendered


def test_glossary_api(tmp_path):
    c = make_client(create_app(Settings(data_dir=tmp_path / "d")))
    r = c.get("/api/glossary")
    assert r.status_code == 200
    g = r.json()
    assert g["version"] == glossary.load()["version"]
    assert any(t["id"] == "reynolds-number" and "stickiness" in t["short"] for t in g["terms"])
