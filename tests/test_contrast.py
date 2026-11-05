"""Colour contrast of the page in both themes (WCAG 2: 4.5:1 for text, 3:1 for large text / UI)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parents[1] / "windlab" / "app" / "static" / "app.css").read_text()

# (foreground, background, minimum ratio) - every text colour on every surface it is used on
PAIRS = [
    ("ink", "card", 4.5), ("ink", "page", 4.5), ("ink", "surface", 4.5), ("ink-2", "card", 4.5),
    ("ink-2", "surface", 4.5), ("ink-2", "page", 4.5), ("muted", "card", 4.5), ("muted", "page", 4.5),
    ("accent-ink", "accent", 4.5), ("accent-fg", "card", 4.5), ("warn-ink", "warn-bg", 4.5),
    ("bad-ink", "bad-bg", 4.5), ("bad-ink", "card", 4.5), ("danger-ink", "danger-bg", 4.5),
    ("ok-ink", "card", 4.5), ("ink", "note-bg", 4.5), ("disabled-ink", "disabled-bg", 4.5),
    ("ink", "input-bg", 4.5),
    # non-text: chart line on its surface, focus ring, wind / up arrows and labels on the 3D view
    ("series-1", "surface", 3.0), ("focus", "card", 3.0), ("wind", "surface", 3.0), ("up", "surface", 3.0),
    ("line-strong", "input-bg", 3.0),
    # canvas charts: tick and axis labels are text; axis lines and every comparison series are graphics;
    # gridlines are deliberately faint but must still be visible on the chart surface
    ("chart-text", "surface", 4.5), ("chart-text", "card", 4.5), ("chart-axis", "surface", 3.0),
    ("series-2", "surface", 3.0), ("series-3", "surface", 3.0), ("series-4", "surface", 3.0),
    ("series-5", "surface", 3.0),
    ("chart-grid", "surface", 1.25),
]


def test_every_css_variable_the_scripts_read_is_defined_in_both_themes():
    static = Path(__file__).resolve().parents[1] / "windlab" / "app" / "static"
    used = set()
    for name in ("app.js", "chart.js", "viewer.js", "preview.js"):
        src = (static / name).read_text()
        used |= set(re.findall(r"""(?:cssVar|cssColor|cssRgb)\(\s*[`"']--([\w-]+\w)[`"']""", src))
        if "--series-${" in src:
            used |= {"series-1", "series-2", "series-3", "series-4", "series-5"}
    assert {"chart-text", "chart-grid", "chart-axis", "surface", "body-fill", "body-edge"} <= used
    for theme, t in (("light", LIGHT), ("dark", DARK)):
        missing = {u for u in used if u not in t and u not in ("cp-neg", "cp-mid", "cp-pos")}
        assert not missing, f"{theme} theme lacks {sorted(missing)}"


def test_series_colours_are_distinct():
    for t in (LIGHT, DARK):
        cols = [t[f"series-{i}"] for i in range(1, 6)]  # one per compared run (MAX_COMPARE)
        assert len(set(cols)) == 5


def _block(selector_regex: str) -> dict[str, str]:
    m = re.search(selector_regex + r"\s*\{(.*?)\}", CSS, re.S | re.M)
    assert m, selector_regex
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))


LIGHT = _block(r"^:root")
DARK = _block(r':root\[data-theme="dark"\]')
DARK_SYSTEM = _block(r':root:where\(:not\(\[data-theme="light"\]\)\)')


def _lum(hexcolor: str) -> float:
    rgb = [int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_known_contrast_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg, bg, minimum", PAIRS)
def test_contrast(theme, fg, bg, minimum):
    t = LIGHT if theme == "light" else DARK
    assert fg in t and bg in t, (fg, bg)
    r = contrast(t[fg], t[bg])
    assert r >= minimum, f"{theme}: --{fg} {t[fg]} on --{bg} {t[bg]} is {r:.2f}:1, needs {minimum}:1"


def test_dark_theme_is_declared_the_same_twice():
    assert DARK == DARK_SYSTEM
    assert set(DARK) == set(LIGHT) - {"cp-neg", "cp-mid", "cp-pos"}  # Cp scale is shared by both themes
