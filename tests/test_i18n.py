"""Translation-ready text (PRD ACC-3): the English catalog, the t() helper's contract, every message the page
scripts and the fixed HTML use, a pseudo-localisation pass, and a scan that fails when a message is left
hard-coded in app.js or preview.js or a text of index.html is not marked with data-i18n.

Server-side messages (check reports, run warnings, expected-error texts, report and CSV headers) and the
static demo's fake-server answers (demo.js) stay English for now; see docs/I18N.md."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "windlab" / "app" / "static"
APP = (STATIC / "app.js").read_text()
I18N = (STATIC / "i18n.js").read_text()
HTML = (STATIC / "index.html").read_text()
CATALOG = json.loads((STATIC / "i18n" / "en.json").read_text())
MESSAGES: dict[str, str] = CATALOG["messages"]
# every script that shows words to the student goes through t(); demo.js answers like the server (English)
SCRIPTS = {name: (STATIC / name).read_text() for name in ("app.js", "preview.js", "viewer.js", "chart.js", "plot.js")}
ALL_JS = "\n".join(SCRIPTS.values())

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def t_mirror(messages: dict[str, str], key: str, params: dict | None = None) -> str:
    """The same substitution rule as i18n.js: {name} filled from params, missing ones left visible."""
    params = params or {}
    s = messages.get(key)
    if s is None:
        return f"⟦{key}⟧"
    return PLACEHOLDER.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), s)


def pseudo_localise(messages: dict[str, str]) -> dict[str, str]:
    """Wrap every message in ⟦ ⟧ and stretch it by about 30 % (accented letters), keeping placeholders and
    the little inline markup (<b>, <sub>, <code>, <span data-...>) some page texts carry."""
    table = str.maketrans("aeiouAEIOU", "àéîõüÀÉÎÕÜ")
    token = re.compile(r"(\{\w+\}|<[^>]+>)")

    def stretch(text: str) -> str:
        out = [part if token.fullmatch(part) else part.translate(table) for part in token.split(text)]
        return "⟦" + "".join(out) + "~" * -(-3 * len(text) // 10) + "⟧"  # 30 % longer, ceiling

    return {k: stretch(v) for k, v in messages.items()}


# --------------------------------------------------------------------------- the catalog itself


def test_catalog_is_well_formed():
    assert CATALOG["locale"] == "en" and len(MESSAGES) > 500
    for key, text in MESSAGES.items():
        assert re.fullmatch(r"[a-z]+(\.[A-Za-z0-9]+)+", key), key
        assert isinstance(text, str) and text.strip(), key
        if key not in ("msg.noServer", "msg.unexpected"):  # these set a command apart with double spaces on purpose
            assert "  " not in text, key
        assert "{}" not in text, key
        for ph in PLACEHOLDER.findall(text):
            assert re.fullmatch(r"[a-z][A-Za-z0-9]*", ph), (key, ph)
        # only the little inline markup richNodes() understands
        for tag in re.findall(r"<(/?[a-z]+)", text):
            assert tag.lstrip("/") in ("b", "strong", "i", "em", "sub", "sup", "code", "span"), (key, tag)


def test_helper_contract_in_i18n_js():
    assert "export async function loadMessages" in I18N and "export function t(key, params = {})" in I18N
    assert "export function nf(x, digits = 3)" in I18N and "Intl.NumberFormat" in I18N
    assert 'return `⟦${key}⟧`' in I18N  # a missing key is visible, never a crash
    assert 'new URL("./i18n/en.json", import.meta.url)' in I18N  # same origin, next to the module
    assert "await loadMessages();" in APP and "applyPageText();" in APP
    assert 'import { currentLocale, hasMessage, loadMessages, nf, nfInt, t } from "./i18n.js";' in APP
    # numbers shown to the student go through Intl.NumberFormat (a CSS percentage for a legend tick may not)
    assert "toFixed(" not in re.sub(r"toFixed\(\d\)\}%", "", APP)
    # the fixed page text: data-i18n elements and data-i18n-attr attributes, inline markup built as nodes
    for must in ('document.querySelectorAll("[data-i18n]")', 'document.querySelectorAll("[data-i18n-attr]")',
                 "function richNodes(text)", "e.replaceChildren(...richNodes(text))", "document.documentElement.lang"):
        assert must in APP, must
    assert ".innerHTML" not in ALL_JS


# --------------------------------------------------------------------------- keys used by the scripts and the page

_T_CALL = re.compile(r"""\bt_?\(\s*(["'])([^"']+)\1""")
# t(condition ? "key.a" : "key.b"[, params]) - both keys count
_T_TERNARY = re.compile(r"""\bt_?\(\s*[^()?]*?\?\s*(["'])([^"']+)\1\s*:\s*(["'])([^"']+)\3""")
# t(a ? "k1" : b ? "k2" : "k3") - the three-way case used for the lesson answer labels
_T_TERNARY3 = re.compile(r"""\bt_?\(\s*[^()?]*?\?\s*"([^"]+)"\s*:\s*[^()?]*?\?\s*"([^"]+)"\s*:\s*"([^"]+)"\s*\)""")
_T_DYNAMIC = re.compile(r"\bt_?\(\s*`([^`]+)`")
_KEY_LITERAL = re.compile(r"""["']([a-z]+(?:\.[A-Za-z0-9]+)+)["']""")
_HTML_KEY = re.compile(r'data-i18n="([^"]+)"')
_HTML_ATTR = re.compile(r'data-i18n-attr="([^"]+)"')


def html_keys() -> set[str]:
    keys = set(_HTML_KEY.findall(HTML))
    for spec in _HTML_ATTR.findall(HTML):
        for pair in spec.split(";"):
            keys.add(pair.split(":")[1].strip())
    return keys


def _used_keys() -> tuple[set[str], list[str]]:
    static = set(m.group(2) for m in _T_CALL.finditer(ALL_JS))
    for m in _T_TERNARY.finditer(ALL_JS):
        static |= {m.group(2), m.group(4)}
    for m in _T_TERNARY3.finditer(ALL_JS):
        static |= {m.group(1), m.group(2), m.group(3)}
    # any other quoted literal that is a catalog key (e.g. a key stored in a table such as PRESET_KEYS)
    static |= {m.group(1) for m in _KEY_LITERAL.finditer(ALL_JS) if m.group(1) in MESSAGES}
    dynamic = [m.group(1) for m in _T_DYNAMIC.finditer(ALL_JS)]
    return static, dynamic


def _dynamic_families() -> set[str]:
    """Keys the template-literal calls can produce (each family is enumerated here on purpose)."""
    out = set()
    for pattern in _used_keys()[1]:
        if pattern.startswith("run.label.${") and "presetChoice()" in pattern:
            out |= {"run.label.quick", "run.label.standard", "run.label.detailed"}
        elif pattern == "run.stage.${state}":
            out |= {"run.stage.finished", "run.stage.now", "run.stage.waiting"}
        elif pattern in ("tour.${tour.index + 1}.title", "tour.${tour.index + 1}.text"):
            out |= {f"tour.{i}.{kind}" for i in range(1, 7) for kind in ("title", "text")}
        elif pattern == "shape.partType.guess.${pt.detected}":
            out |= {"shape.partType.guess.wing", "shape.partType.guess.body"}
        elif pattern == "shape.kind.${r.value}":
            out |= {"shape.kind.wing", "shape.kind.body"}
        else:
            raise AssertionError(f"unknown dynamic t() key pattern {pattern!r}: add its family to this test")
    return out


def test_every_key_used_exists_and_every_message_is_used():
    static, _ = _used_keys()
    used = static | _dynamic_families() | html_keys()
    missing = sorted(used - set(MESSAGES))
    assert not missing, f"the scripts or index.html use keys that i18n/en.json lacks: {missing}"
    unused = sorted(set(MESSAGES) - used)
    assert not unused, f"i18n/en.json has messages no code uses: {unused}"


def _param_names(obj_src: str) -> set[str]:
    """Top-level keys of a JS object literal source '{ a, b: x, c: f(1, 2) }'."""
    names, depth, token, in_str = set(), 0, "", None
    i = 0
    while i < len(obj_src):
        ch = obj_src[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 1 and ch in ",:":
            name = token.strip()
            if re.fullmatch(r"[A-Za-z_]\w*", name):
                names.add(name)
            token = ""
            if ch == ":":  # skip the value up to the next top-level comma
                j, d, s = i + 1, 0, None
                while j < len(obj_src):
                    c = obj_src[j]
                    if s:
                        if c == "\\":
                            j += 2
                            continue
                        if c == s:
                            s = None
                    elif c in "\"'`":
                        s = c
                    elif c in "([{":
                        d += 1
                    elif c in ")]}":
                        if d == 0:
                            break
                        d -= 1
                    elif c == "," and d == 0:
                        break
                    j += 1
                i = j
                continue
        if depth == 1 and not in_str and ch not in ",:{":
            token += ch
        i += 1
    name = token.strip()
    if re.fullmatch(r"[A-Za-z_]\w*", name):
        names.add(name)
    return names


def _calls_with_params():
    """(keys, parameter names) for every t("key", {...}) / t(c ? "a" : "b", {...}) call with an object literal."""
    out = []
    pattern = (r"""\bt_?\(\s*(?:(["'])(?P<k>[^"']+)\1|[^()?]*?\?\s*(["'])(?P<a>[^"']+)\3\s*:\s*(["'])(?P<b>[^"']+)\5)"""
               r"""\s*,\s*\{""")
    for m in re.finditer(pattern, ALL_JS):
        keys = [m.group("k")] if m.group("k") else [m.group("a"), m.group("b")]
        start = m.end() - 1
        depth, i, in_str = 0, start, None
        while i < len(ALL_JS):
            ch = ALL_JS[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in "\"'`":
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((keys, _param_names(ALL_JS[start:i + 1])))
    return out


def test_placeholders_match_the_parameters_passed():
    seen = set()
    for keys, names in _calls_with_params():
        union = set()
        for key in keys:
            seen.add(key)
            wanted = set(PLACEHOLDER.findall(MESSAGES[key]))
            union |= wanted
            assert wanted <= names, f"t({key!r}) does not pass {sorted(wanted - names)}"
        assert names <= union, f"t({keys!r}) passes {sorted(names - union)} that the message(s) do not use"
    # messages with placeholders are always called with parameters (the dynamic families have none)
    for key, text in MESSAGES.items():
        if PLACEHOLDER.search(text):
            assert key in seen, f"{key} has placeholders but is never called with parameters"


# --------------------------------------------------------------------------- pseudo-localisation


def test_pseudo_localisation_keeps_every_placeholder_and_expands():
    pseudo = pseudo_localise(MESSAGES)
    for key, text in MESSAGES.items():
        names = PLACEHOLDER.findall(text)
        sample = {n: f"<{n}>" for n in names}
        out = t_mirror(pseudo, key, sample)
        assert out.startswith("⟦") and out.endswith("⟧"), key
        assert "{" not in out and "}" not in out, (key, out)  # every placeholder was filled
        for n in names:
            assert f"<{n}>" in out, (key, n)
        assert re.findall(r"<[^>]+>", text) == re.findall(r"<[^>]+>", pseudo[key]), key  # inline markup kept as is
        letters = len(re.sub(r"\{\w+\}|<[^>]+>|[^A-Za-z]", "", text))
        if letters >= 10:  # the layout must tolerate a 25 % longer string (ACC-3)
            assert len(out) - 2 >= len(text) * 1.2, key
    assert t_mirror(MESSAGES, "nope.missing") == "⟦nope.missing⟧"
    assert t_mirror(MESSAGES, "run.queued", {"stage": "Solving"}) == "Solving (number {position} in line)"


# --------------------------------------------------------------------------- no hard-coded strings left

_HARDCODED = re.compile(
    r"""(setText\(\s*"[\w-]+"\s*,\s*|shapeError\(\s*|lessonNote\(\s*|_note\(\s*|\{\s*error:\s*|textContent:\s*|"""
    r"""setAttribute\("aria-label",\s*|\.textContent\s*=\s*|placeholder:\s*)(["'`])(?P<text>[^"'`]*)\2"""
)
# literals that are not language: symbols, units, empty strings, numbers
_ALLOWED = re.compile(r"^[\s\d.,:;%°×+\-–—≤≥()/|?]*(m/s|m²|°)?[\s\d.,:;%°×+\-–—≤≥()/|?]*$")


@pytest.mark.parametrize("name", ["app.js", "preview.js", "viewer.js", "chart.js"])
def test_no_message_is_hard_coded(name):
    leftovers = []
    src = SCRIPTS[name]
    for m in _HARDCODED.finditer(src):
        text = m.group("text")
        if text.startswith("${") or _ALLOWED.match(text) or not re.search(r"[A-Za-z]{2,}", text):
            continue
        line = src.count("\n", 0, m.start()) + 1
        leftovers.append(f"{line}: {m.group(0)[:90]}")
    hint = f"hard-coded messages in {name} (move them to i18n/en.json and use t()):\n"
    assert not leftovers, hint + "\n".join(leftovers)


def test_only_the_server_down_fallbacks_are_english_literals():
    """The two messages shown when the catalog itself could not load keep an English fallback, and nothing else."""
    assert "FALLBACK_NO_SERVER" in APP and "FALLBACK_UNEXPECTED" in APP
    assert 'hasMessage("msg.noServer") ? t("msg.noServer") : FALLBACK_NO_SERVER' in APP
    assert MESSAGES["msg.noServer"].startswith("WindLab's server is not answering")


@pytest.mark.parametrize("text", ["Choose a shape first.", "Stopping the run…", "No iterations yet.",
                                  "lift stops rising", "Use all bodies", "Show progress", "Delete this run for good?",
                                  "Start this lesson", "Check my answer", "Background reading",
                                  "The preview starts again from still air.", "Ctrl + scroll to zoom"])
def test_the_old_literals_are_gone_from_the_scripts(text):
    assert f'"{text}' not in ALL_JS and any(text in v for v in MESSAGES.values())


# --------------------------------------------------------------------------- the fixed text of index.html

VOID = {"input", "meta", "link", "br", "hr", "img", "source", "wbr", "col"}
INLINE = {"b", "strong", "i", "em", "sub", "sup", "code", "span"}
LETTERS = re.compile(r"[A-Za-z]{2,}")
BRAND = {"WindLab"}


class _Page(HTMLParser):
    """Text nodes with their data-i18n context, and each data-i18n element's normalised content."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[dict] = []
        self.loose: list[str] = []  # text with letters outside any data-i18n element
        self.marked: list[tuple[str, str]] = []  # (key, inner source normalised)
        self.attrs: list[tuple[str, str, str]] = []  # (attr, key, English value)
        self.src = ""

    def feed_all(self, src: str):
        self.src = src
        self.lines = [0]
        for m in re.finditer("\n", src):
            self.lines.append(m.end())
        self.feed(src)
        self.close()

    def pos(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        node = {"tag": tag, "key": a.get("data-i18n"), "start": self.pos() + len(self.get_starttag_text()),
                "code": tag in ("code", "script", "style") or (self.stack and self.stack[-1]["code"])}
        if a.get("data-i18n-attr"):
            for pair in a["data-i18n-attr"].split(";"):
                attr, key = (x.strip() for x in pair.split(":"))
                self.attrs.append((attr, key, a.get(attr, "")))
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack:
            node = self.stack.pop()
            if node["tag"] == tag:
                if node["key"]:
                    inner = self.src[node["start"]:self.pos()]
                    self.marked.append((node["key"], " ".join(inner.split())))
                break

    def handle_data(self, data):
        text = " ".join(data.split())
        if not LETTERS.search(text) or text in BRAND:
            return
        if any(n["key"] for n in self.stack) or any(n["code"] for n in self.stack) or not self.stack:
            return
        self.loose.append(text)


def test_every_text_of_the_page_is_in_the_catalog():
    page = _Page()
    page.feed_all(HTML)
    assert not page.loose, "text in index.html outside any data-i18n element:\n" + "\n".join(page.loose)
    assert len(page.marked) > 250 and len(page.attrs) > 60
    for key, inner in page.marked:
        assert key in MESSAGES, key
        assert MESSAGES[key] == inner, f"{key}: the catalog says {MESSAGES[key]!r} but index.html says {inner!r}"
    for attr, key, value in page.attrs:
        assert key in MESSAGES and MESSAGES[key] == " ".join(value.split()), (attr, key)
    # the inline markup inside a message is only the kinds richNodes() builds, and never a script
    assert "<script" not in "".join(MESSAGES.values())
