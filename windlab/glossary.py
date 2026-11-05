"""The glossary (PRD UX-1): one JSON source for the page, the lessons and docs/GLOSSARY.md."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

GLOSSARY_PATH = Path(__file__).with_name("glossary.json")
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@lru_cache(maxsize=1)
def load() -> dict:
    data = json.loads(GLOSSARY_PATH.read_text())
    check(data)
    return data


def terms() -> list[dict]:
    return list(load()["terms"])


def by_id() -> dict[str, dict]:
    return {t["id"]: t for t in load()["terms"]}


def check(data: dict) -> None:
    """Every term has an id, a one-sentence 'short' and only cross-references that exist."""
    ids = set()
    for t in data["terms"]:
        if not _ID.match(t.get("id", "")):
            raise ValueError(f"bad glossary id {t.get('id')!r}")
        if t["id"] in ids:
            raise ValueError(f"duplicate glossary id {t['id']!r}")
        ids.add(t["id"])
        if not t.get("term") or not t.get("short"):
            raise ValueError(f"glossary term {t['id']!r} needs 'term' and 'short'")
    for t in data["terms"]:
        for ref in t.get("see", []):
            if ref not in ids:
                raise ValueError(f"glossary term {t['id']!r} refers to unknown {ref!r}")


def markdown() -> str:
    """docs/GLOSSARY.md, rendered from the JSON (scripts/render_glossary.py keeps it in step)."""
    g = load()
    lines = ["# WindLab glossary", "",
             f"Rendered from `windlab/glossary.json` (version {g['version']}); do not edit by hand. The same",
             "definitions appear in the app behind every (?) button and in the Glossary panel, and in the lessons.", ""]
    for t in sorted(g["terms"], key=lambda t: t["term"].lower()):
        alias = f" *({', '.join(t['aliases'])})*" if t.get("aliases") else ""
        lines.append(f"## {t['term']}{alias}")
        lines.append("")
        lines.append(t["short"])
        if t.get("more"):
            lines.append("")
            lines.append(t["more"])
        if t.get("see"):
            names = by_id()
            lines.append("")
            lines.append("See also: " + ", ".join(names[s]["term"] for s in t["see"]) + ".")
        lines.append("")
    return "\n".join(lines)
