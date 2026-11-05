#!/usr/bin/env python3
"""Write docs/GLOSSARY.md from windlab/glossary.json (tests check the two agree)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from windlab import glossary  # noqa: E402

if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "docs" / "GLOSSARY.md"
    out.write_text(glossary.markdown())
    print(f"wrote {out} ({len(glossary.terms())} terms)")
