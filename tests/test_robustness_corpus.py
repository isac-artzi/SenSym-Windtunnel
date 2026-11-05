"""The robustness corpus (task 011, step 2): the recorded results, and that the generator still builds parts
that pass the STEP import (needs OpenCASCADE; the solver runs themselves are not repeated here)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import robustness_corpus as rc  # noqa: E402

RESULTS = ROOT / "validation" / "robustness" / "results.json"


def test_recorded_corpus_all_meshed_and_solved():
    data = json.loads(RESULTS.read_text())
    rows = {r["part"]: r for r in data["rows"]}
    assert set(rows) == set(rc.CORPUS), "every corpus part has a recorded row"
    for name, r in rows.items():
        assert r["status"] == "done", (name, r.get("error"))
        assert r["usable"] and r["stl_twin"]["usable"], name
        assert 50_000 < r["mesh"]["cells"] < 1_500_000 and r["mesh"]["max_non_orthogonality"] < 70, name
        assert r["layers"]["thickness_pct"] > 60, name
        assert r["result"]["cd"] is not None and 0.01 < r["result"]["cd"] < 2.0, name
    md = (ROOT / "validation" / "robustness.md").read_text()
    assert "10 of 10 parts meshed and solved" in md and "Notes, failures and fixes" in md
    for part in rc.CORPUS:
        assert f"| {part} |" in md and (ROOT / "validation" / "robustness" / "parts" / f"{part}.step").is_file()


@pytest.mark.skipif(not (ROOT / "windlab").is_dir(), reason="repo layout")
def test_generator_parts_pass_the_step_import(tmp_path):
    pytest.importorskip("OCP")
    from tests import stepgen as g
    from windlab.geometry import step

    parts = rc.build_parts(g, ["rocket", "assembly", "plate-2mm"])
    for name, p in parts.items():
        f = g.write_step(tmp_path / f"{name}.step", p["shape"], "mm")
        r = step.check_step(f, None, output=tmp_path / f"{name}.body.stl", bodies=p["bodies"])
        assert r.ok and r.units == "mm", (name, r.problems)
    assert parts["rocket"]["shape"] is not None
    r = step.check_step(tmp_path / "rocket.step", None, output=tmp_path / "r2.stl")
    assert r.source["solids"] == 1 and r.size_m[1] == pytest.approx(0.12, abs=1e-3)  # fins included
    r = step.check_step(tmp_path / "assembly.step", None, output=tmp_path / "a2.stl", bodies="all")
    assert r.pieces == 2 and r.source["bodies_available"] == 2
