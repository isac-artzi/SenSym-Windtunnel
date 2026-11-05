"""STEP import (PRD GEO-2): the worker, the units, several solids, the API, and life without OCP."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.asgi_client import make_client
from windlab import doctor
from windlab.app import Settings, create_app
from windlab.geometry import _step_worker as worker
from windlab.geometry import step
from windlab.geometry.upload import UploadError

HAVE_OCP = step.available()
needs_ocp = pytest.mark.skipif(not HAVE_OCP, reason="OpenCASCADE (cadquery-ocp) is not installed")


@pytest.fixture(scope="module")
def steps(tmp_path_factory):
    """STEP files written by OpenCASCADE (shapes built in mm, written in the unit the test wants)."""
    pytest.importorskip("OCP")
    from tests import stepgen as g

    d = tmp_path_factory.mktemp("step")
    files = {
        "box_mm": g.write_step(d / "box_mm.step", g.box(300, 100, 20), "mm"),
        "cylinder": g.write_step(d / "cylinder.stp", g.cylinder(20, 200, "x"), "mm"),
        "touching": g.write_step(d / "touching.step", g.compound(g.box(100, 50, 50),
                                                                    g.box(100, 50, 50, at=(100, 0, 0))), "mm"),
        "separate": g.write_step(d / "separate.step", g.compound(g.box(100, 50, 50),
                                                                    g.box(30, 30, 30, at=(200, 0, 0))), "mm"),
        "box_m": g.write_step(d / "box_m.step", g.box(300, 100, 20), "m"),
        "box_in": g.write_step(d / "box_in.step", g.box(304.8, 101.6, 25.4), "in"),
        "box_ft": g.write_step(d / "box_ft.step", g.box(304.8, 152.4, 30.48), "ft"),
        "surfaces": g.write_step(d / "surfaces.step", g.faces_only(g.box(100, 100, 100)), "mm"),
        "fillets": g.write_step(d / "fillets.step", g.fillet_all(g.box(200, 80, 30), 5.0), "mm"),
    }
    return files


def _read_binary(path: Path) -> np.ndarray:
    data = path.read_bytes()
    n = int.from_bytes(data[80:84], "little")
    assert len(data) == 84 + 50 * n
    return np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                         count=n, offset=84)["v"].astype(float)


# --------------------------------------------------------------------------- the worker and the units


@needs_ocp
def test_box_in_millimetres(steps, tmp_path):
    out = tmp_path / "body.stl"
    r = step.check_step(steps["box_mm"], None, output=out)
    assert r.ok and r.format == "step" and r.units == "mm" and r.triangles == 12 and r.pieces == 1
    assert r.size_m == pytest.approx((0.3, 0.1, 0.02), abs=1e-6) and not r.size_suspicious
    assert r.source["unit_declared"] == "mm" and r.source["units_from_file"] and r.source["solids"] == 1
    assert r.source["faces"] == 6 and not r.source["fused"] and r.source["bodies_available"] == 1
    assert any("says it is in millimetres" in m for m in r.messages)
    assert any("1 solid body with 6 faces" in m for m in r.messages)
    v = _read_binary(out)
    assert v.max() == pytest.approx(0.3, rel=1e-6)  # the body.stl is in metres, like every upload


@needs_ocp
def test_student_units_override_the_file(steps, tmp_path):
    r = step.check_step(steps["box_mm"], "cm", output=tmp_path / "body.stl")
    assert r.units == "cm" and r.size_m[0] == pytest.approx(3.0, abs=1e-6) and not r.source["units_from_file"]
    assert any("file says millimetres, but you chose centimetres" in m for m in r.messages)


@needs_ocp
def test_metre_inch_and_foot_headers(steps, tmp_path):
    r = step.check_step(steps["box_m"], None, output=tmp_path / "a.stl")
    assert r.units == "m" and r.source["unit_declared"] == "m" and r.size_m[0] == pytest.approx(0.3, abs=1e-6)
    r = step.check_step(steps["box_in"], None, output=tmp_path / "b.stl")
    assert r.units == "in" and r.source["unit_declared"] == "in" and r.source["unit_name_in_file"] == "inch"
    assert r.size_m == pytest.approx((0.3048, 0.1016, 0.0254), abs=1e-6)
    # a unit WindLab does not offer (feet): converted to metres, said so, units choice = metres
    r = step.check_step(steps["box_ft"], None, output=tmp_path / "c.stl")
    assert r.units == "m" and r.source["converted_to_metres"] and r.source["unit_name_in_file"] == "foot"
    assert r.size_m[0] == pytest.approx(0.3048, abs=1e-6)
    assert any("converted it to metres" in m for m in r.messages)


def test_declared_unit_parser():
    mm = b"#1 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\n"
    assert worker.declared_unit(mm) == {"unit": "mm", "name": "millimetre", "factor": 1e-3}
    assert worker.declared_unit(b"#1=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.));")["unit"] == "m"
    inch = (b"#5 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\n"
            b"#6 = ( CONVERSION_BASED_UNIT('INCH',#7) LENGTH_UNIT() NAMED_UNIT(#8) );\n")
    assert worker.declared_unit(inch) == {"unit": "in", "name": "inch", "factor": 0.0254}  # the named unit wins
    assert worker.declared_unit(b"#6 = ( CONVERSION_BASED_UNIT('FOOT',#7) LENGTH_UNIT() NAMED_UNIT(#8) );")[
        "unit"] is None
    assert worker.declared_unit(b"nothing here") == {"unit": None, "name": None, "factor": None}


@needs_ocp
def test_cylinder_is_tessellated_finely_enough(steps, tmp_path):
    r = step.check_step(steps["cylinder"], None, output=tmp_path / "body.stl")
    assert r.ok and r.size_m == pytest.approx((0.2, 0.04, 0.04), abs=1e-4)
    assert r.triangles > 60 and r.self_intersecting is False
    # chord error of the round faces stays below 1/1000 of the diagonal (about 0.2 mm here)
    assert r.volume_m3 == pytest.approx(np.pi * 0.02**2 * 0.2, rel=0.02)


@needs_ocp
def test_touching_solids_are_fused_into_one(steps, tmp_path):
    r = step.check_step(steps["touching"], None, output=tmp_path / "body.stl")
    assert r.ok and r.pieces == 1 and r.manifold and r.source["solids"] == 2 and r.source["fused"]
    assert r.source["bodies_available"] == 1 and r.size_m[0] == pytest.approx(0.2, abs=1e-6)
    assert any("touch each other, so WindLab joined them into one solid" in m for m in r.messages)
    assert not any("separate" in w for w in r.warnings)


@needs_ocp
def test_separate_solids_largest_by_default_or_all(steps, tmp_path):
    r = step.check_step(steps["separate"], None, output=tmp_path / "a.stl")
    assert r.ok and r.pieces == 1 and r.source["bodies_available"] == 2 and r.source["bodies_used"] == "largest"
    assert r.size_m[0] == pytest.approx(0.1, abs=1e-6)
    assert r.warnings and "2 separate bodies that do not touch" in r.warnings[0]
    assert "largest one (10 cm long)" in r.warnings[0]
    sizes = [b["longest"] for b in r.source["bodies"]]  # mm, largest first (bounding boxes carry a tiny tolerance)
    assert sizes == pytest.approx([100.0, 30.0], abs=0.01)
    r = step.check_step(steps["separate"], None, output=tmp_path / "b.stl", bodies="all")
    assert r.ok and r.pieces == 2 and r.source["bodies_used"] == "all" and r.size_m[0] == pytest.approx(0.23, abs=1e-6)
    assert "all of them are in the wind tunnel together" in r.warnings[0]
    with pytest.raises(UploadError):
        step.check_step(steps["separate"], None, output=tmp_path / "c.stl", bodies="some")


@needs_ocp
def test_surfaces_without_a_solid_are_explained(steps, tmp_path):
    out = tmp_path / "body.stl"
    r = step.check_step(steps["surfaces"], None, output=out)
    assert not r.ok and r.source["solids"] == 0 and not out.exists()
    assert "contains no solid body, only surfaces" in r.problems[0] and not r.watertight


@needs_ocp
def test_fillets_and_healing_report(steps, tmp_path):
    r = step.check_step(steps["fillets"], None, output=tmp_path / "body.stl")
    assert r.ok and r.source["faces"] == 26 and r.triangles > 500 and r.source["valid_after"] is True
    assert r.source["healed"] is False  # a clean OpenCASCADE solid needs no repair
    assert not any("repaired" in m for m in r.messages)


@needs_ocp
def test_triangle_cap_coarsens_then_refuses(steps, tmp_path):
    r = step.check_step(steps["fillets"], None, output=tmp_path / "a.stl", max_triangles=400)
    assert r.ok and r.triangles <= 400  # coarsened to fit
    with pytest.raises(UploadError, match="too much detail"):
        step.check_step(steps["fillets"], None, output=tmp_path / "b.stl", max_triangles=10)


@needs_ocp
def test_garbage_and_limits(tmp_path):
    bad = tmp_path / "bad.step"
    bad.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1 = NONSENSE();\nENDSEC;\nEND-ISO-10303-21;\n")
    with pytest.raises(UploadError, match="STEP"):
        step.check_step(bad, None, output=tmp_path / "a.stl")
    notstep = tmp_path / "x.step"
    notstep.write_bytes(b"hello " * 100)
    with pytest.raises(UploadError, match="not a STEP file"):
        step.check_step(notstep, None, output=tmp_path / "b.stl")
    with pytest.raises(UploadError, match="limit"):
        step.check_step(notstep, None, output=tmp_path / "c.stl", max_bytes=10)
    with pytest.raises(UploadError, match="too long"):
        step.check_step(notstep, None, output=tmp_path / "d.stl", wall_seconds=0.001)
    with pytest.raises(UploadError, match="units"):
        step.check_step(notstep, "furlongs", output=tmp_path / "e.stl")


def test_is_step_file_by_content_then_by_name(tmp_path):
    p = tmp_path / "part.bin"
    p.write_bytes(b"ISO-10303-21;\nHEADER;")
    assert step.is_step_file(p, None)
    p.write_bytes(b"solid x\nendsolid x\n")
    assert not step.is_step_file(p, "part.step")  # a text STL named .step is still an STL
    p.write_bytes(b"\x00" * 200)
    assert not step.is_step_file(p, "part.step")  # a binary STL
    p.write_bytes(b"x")
    assert step.is_step_file(p, "part.STP") and not step.is_step_file(p, "part.stl")


# --------------------------------------------------------------------------- without OpenCASCADE


def test_without_ocp_everything_degrades_to_a_clear_message(monkeypatch, tmp_path):
    monkeypatch.setattr(step, "available", lambda: False)
    step.probe.cache_clear()
    with pytest.raises(UploadError, match="STEP import is not installed"):
        step.check_step(tmp_path / "any.step", None, output=tmp_path / "body.stl")
    c = doctor.check_step_import()
    assert c.status == "warn" and "not available" in c.message and "libgl1" in c.fix
    step.probe.cache_clear()
    app = create_app(Settings(data_dir=tmp_path / "data"))
    client = make_client(app)
    cfg = client.get("/api/config").json()
    assert cfg["step_import"]["available"] is False and "STL" in cfg["step_import"]["message"]
    r = client.post("/api/geometry", params={"name": "part.step"}, content=b"ISO-10303-21;\nHEADER;\n" * 10)
    assert r.status_code == 400 and "STEP import is not installed" in r.json()["detail"]
    app.fastapi.state.runs.stop()


def test_doctor_reports_the_probe():
    assert doctor.check_step_import(lambda: (True, "OpenCASCADE 8.0 (cadquery-ocp)")).status == "pass"
    c = doctor.check_step_import(lambda: (False, "the OpenCASCADE package (cadquery-ocp) is not installed"))
    assert c.status == "warn" and "pip install" in c.fix


@needs_ocp
def test_probe_loads_ocp_in_a_child_process():
    step.probe.cache_clear()
    ok, detail = step.probe()
    assert ok and "OpenCASCADE" in detail


# --------------------------------------------------------------------------- through the API


@needs_ocp
def test_upload_step_through_the_api(steps, tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data"))
    client = make_client(app)
    try:
        assert client.get("/api/config").json()["step_import"]["available"] is True
        r = client.post("/api/geometry", params={"name": "bracket.STEP"}, content=steps["box_mm"].read_bytes())
        assert r.status_code == 200, r.text
        g = r.json()
        assert g["usable"] and g["units"] == "mm" and g["format"] == "step" and g["source"]["units_from_file"]
        assert g["longest_m"] == pytest.approx(0.3) and g["reference"]["length"] == pytest.approx(0.3)
        assert g["source"]["unit_declared"] == "mm" and g["source"]["bodies_available"] == 1
        # the stored body.stl is a normal metre-scaled STL: preview and section work as for any upload
        assert client.get(f"/api/geometry/{g['geometry_id']}/preview").status_code == 200
        assert client.get(f"/api/geometry/{g['geometry_id']}/section").status_code == 200
        # the student's units win over the file's
        g2 = client.post("/api/geometry", params={"name": "bracket.step", "units": "m"},
                         content=steps["box_mm"].read_bytes()).json()
        assert g2["units"] == "m" and g2["size_suspicious"] and g2["suggested_units"] == "mm"
        # separate bodies: largest, then all
        g3 = client.post("/api/geometry", params={"name": "asm.step"}, content=steps["separate"].read_bytes()).json()
        assert g3["source"]["bodies_used"] == "largest" and g3["pieces"] == 1
        g4 = client.post("/api/geometry", params={"name": "asm.step", "bodies": "all"},
                         content=steps["separate"].read_bytes()).json()
        assert g4["source"]["bodies_used"] == "all" and g4["pieces"] == 2
        assert client.post("/api/geometry", params={"name": "asm.step", "bodies": "some"},
                           content=b"x").status_code == 422
        # an STL still needs its units
        from tests.test_app import cube_bytes
        r = client.post("/api/geometry", params={"name": "cube.stl"}, content=cube_bytes(tmp_path))
        assert r.status_code == 400 and "units" in r.json()["detail"]
        # nothing left behind in the geometry folder but the usual files
        folder = app.fastapi.state.geometry.folder(g["geometry_id"])
        assert sorted(p.name for p in folder.iterdir()) == ["body.stl", "geometry.json"]
        meta = json.loads((folder / "geometry.json").read_text())
        assert meta["report"]["source"]["format"] == "step"
    finally:
        app.fastapi.state.runs.stop()
