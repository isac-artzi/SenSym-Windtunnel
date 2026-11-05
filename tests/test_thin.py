"""Thin plates and fins (task 013): the worker's thickness estimate, the mesh decision, the gap refinement
in the snappyHexMesh template, the notes on the page and in the report, and the quieter symmetry note."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from tests.asgi_client import make_client
from tests.test_app import CUBE_F, CUBE_V, FakeSolver, naca
from windlab import case_builder as cb
from windlab import meshcache
from windlab.app import Settings, create_app
from windlab.app.runs import symmetry_notes, thin_notes
from windlab.geometry import _stl_worker as worker
from windlab.geometry import thin, upload
from windlab.geometry.stl import write_binary_stl

# --------------------------------------------------------------------------- shapes (mm)


def box_tris(size, at=(0.0, 0.0, 0.0)) -> np.ndarray:
    v = CUBE_V * np.asarray(size, float) + np.asarray(at, float)
    return v[CUBE_F]


def plate_2mm() -> np.ndarray:
    """200 x 100 x 2 mm plate, edge-on to the wind (the corpus part). Its band would add about 430,000 cells,
    over the budget, so since task 014 the Quick mesh is left plain around it."""
    return box_tris((200.0, 100.0, 2.0), at=(0.0, -50.0, -1.0))


def strip_2mm() -> np.ndarray:
    """200 x 20 x 2 mm strip, edge-on: as thin as the plate for the same mesh, a fifth of the area, so its band
    (about 85,000 cells) is within the budget and it gets the refinement."""
    return box_tris((200.0, 20.0, 2.0), at=(0.0, -10.0, -1.0))


def body_with_fins() -> np.ndarray:
    """A 300 x 40 x 40 mm body and four separate 2 mm fins (closed boxes each; the thickness estimate does
    not need them fused): 60 mm long, 40 mm tall, like the corpus rocket's."""
    parts = [box_tris((300.0, 40.0, 40.0), at=(0.0, -20.0, -20.0))]
    for (y, z, sy, sz) in ((-1.0, 20.0, 2.0, 40.0), (-1.0, -60.0, 2.0, 40.0), (20.0, -1.0, 40.0, 2.0),
                           (-60.0, -1.0, 40.0, 2.0)):
        parts.append(box_tris((60.0, sy, sz), at=(240.0, y, z)))
    return np.vstack(parts)


def wedge_tris(length=300.0, span=600.0, half_angle_deg=15.0) -> np.ndarray:
    """A triangular prism: sharp edge along y, faces meeting at 2 x half_angle (a trailing edge, not a plate)."""
    h = length * math.tan(math.radians(half_angle_deg))
    v = np.array([[0, 0, 0], [length, 0, h], [length, 0, -h], [0, span, 0], [length, span, h], [length, span, -h]],
                 float)
    f = np.array([[0, 2, 1], [3, 4, 5], [0, 1, 4], [0, 4, 3], [0, 3, 5], [0, 5, 2], [1, 2, 5], [1, 5, 4]])
    tris = v[f]
    # orient outwards (the centroid of the prism is inside)
    c = v.mean(axis=0)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    flip = np.einsum("ij,ij->i", n, tris.mean(axis=1) - c) < 0
    tris[flip] = tris[flip][:, ::-1]
    return tris


def measure(tris: np.ndarray, **kw) -> dict:
    stats, keep, _faces, _tol = worker.analyse(tris, np.zeros((len(tris), 3)))
    return worker.thickness(tris, keep, outward=stats["signed_volume"] > 0, **kw), stats


# --------------------------------------------------------------------------- the worker's estimate


def test_plate_thickness_is_its_thickness():
    th, stats = measure(plate_2mm())
    assert th["status"] == "done" and th["rays"] == 12
    assert th["min_plate"] == pytest.approx(2.0, rel=1e-6) and th["min"] == pytest.approx(2.0, rel=1e-6)
    plate_area = sum(a for _, a in th["plate"])
    assert plate_area == pytest.approx(2 * 200 * 100, rel=1e-6)  # both big faces; the edges look across the plate
    assert plate_area / stats["surface_area"] > 0.95


def test_cube_has_no_thin_feature_and_a_wedge_is_not_a_plate():
    th, _ = measure(box_tris((300.0, 300.0, 300.0)))
    assert th["status"] == "done" and th["hits"] == 0 and th["plate"] == [] and th["min"] is None
    th, _ = measure(wedge_tris())  # faces 30 degrees apart: a trailing edge, not a plate
    assert th["hits"] > 0 and th["min"] < 100.0
    assert th["plate"] == [] and th["min_plate"] is None
    th, _ = measure(wedge_tris(half_angle_deg=5.0))  # faces 10 degrees apart: within the plate tolerance
    assert th["plate"] and th["min_plate"] == th["min"]


def test_fins_on_a_body_are_found_with_their_area():
    tris = body_with_fins()
    th, stats = measure(tris)
    assert th["min_plate"] == pytest.approx(2.0, rel=1e-6)
    thin_area = sum(a for t, a in th["plate"] if t < 3.0)
    assert thin_area == pytest.approx(4 * 2 * 60 * 40, rel=0.02)  # the two big faces of each fin
    # rays stop at a tenth of the diagonal (34 mm here): the body's 40 mm walls are not "thin" and not measured
    assert th["max_ray"] < 40.0 and all(t <= th["max_ray"] for t, _ in th["plate"])


def test_inside_out_surface_is_measured_the_same():
    tris = plate_2mm()[:, ::-1]  # flipped winding: inside out
    stats, keep, _f, _t = worker.analyse(tris, np.zeros((len(tris), 3)))
    assert stats["signed_volume"] < 0
    th = worker.thickness(tris, keep, outward=False)
    assert th["min_plate"] == pytest.approx(2.0, rel=1e-6)


def test_sampling_and_time_limit():
    tris = plate_2mm()
    th, _ = measure(tris, max_rays=8)  # fewer rays than triangles: area-weighted sample, areas rescaled
    assert th["rays"] == 8 and sum(a for _, a in th["plate"]) <= 2 * 200 * 100 * 1.01 + 1e-6
    th, _ = measure(tris, seconds=0.0)
    assert th["status"] == "timed out" and th["rays"] == 0 and th["plate"] == []
    assert worker.thickness(tris, np.zeros(len(tris), bool), outward=True)["status"] == "skipped"


def test_merge_samples_keeps_area_and_order():
    t = np.linspace(1.0, 10.0, 1000)
    w = np.ones(1000)
    pairs = worker._merge_samples(t, w, 16)
    assert len(pairs) == 16 and sum(a for _, a in pairs) == pytest.approx(1000.0)
    assert [p[0] for p in pairs] == sorted(p[0] for p in pairs)
    assert worker._merge_samples(np.zeros(0), np.zeros(0), 16) == []


def test_report_scales_the_thickness_to_metres(tmp_path):
    p = tmp_path / "plate.stl"
    write_binary_stl(p, CUBE_V * [200.0, 100.0, 2.0], CUBE_F)
    r = upload.check_stl(p, "mm")
    assert r.ok and r.thickness and r.thickness["status"] == "done"
    assert r.thickness["min_plate"] == pytest.approx(0.002, rel=1e-6)
    assert sum(a for _, a in r.thickness["plate"]) == pytest.approx(0.04, rel=1e-6)
    assert r.thickness["plate"][0][0] <= r.thickness["plate"][-1][0]
    s = r.thickness["samples"]
    assert len(s["index"]) == len(s["t"]) == len(s["plate"]) == 4 and max(s["t"]) == pytest.approx(0.002)
    # an open surface is not measured at all
    r2 = upload.build_report({"triangles": 12, "format": "binary", "open_edges": 2, "nonmanifold_edges": 0,
                              "inconsistent_edges": 0, "degenerate_triangles": 0, "pieces": 1, "vertices": 8,
                              "edges": 18, "flipped_stored_normals": 0, "bounds_min": [0, 0, 0],
                              "bounds_max": [1, 1, 1], "surface_area": 6.0, "signed_volume": 1.0}, "m")
    assert r2.thickness is None
    assert upload._scale_thickness({"status": "skipped"}, 1.0) is None
    scaled = upload._scale_thickness({"status": "done", "rays": 3, "hits": 1, "min": 2.0, "min_plate": None,
                                      "max_ray": 10.0, "plate": [[3.0, 4.0], ["x", 1], [1.0, -1.0], [0.5, 2.0]]}, 0.5)
    assert scaled["min"] == 1.0 and scaled["min_plate"] is None and scaled["plate"] == [[0.25, 0.5], [1.5, 1.0]]


# --------------------------------------------------------------------------- the decision


def plate_thickness_m() -> tuple[dict, float]:
    th, stats = measure(plate_2mm())
    return upload._scale_thickness(th, 1e-3), stats["surface_area"] * 1e-6


def strip_thickness_m() -> tuple[dict, float]:
    th, stats = measure(strip_2mm())
    return upload._scale_thickness(th, 1e-3), stats["surface_area"] * 1e-6


QUICK_CELL_02 = 0.2 * 2 / 256  # Quick surface cell for a 0.2 m reference length: 1.56 mm
NO_BUDGET = 10**9


def test_thin_features_depend_on_the_cell_size():
    th, area = plate_thickness_m()
    # the corpus plate: reference length 0.2 m, Quick surface cell 1.56 mm: 2 mm is 1.3 cells. Without a budget
    # one extra level is enough for the cells to be smaller than the plate
    found = thin.thin_features(th, QUICK_CELL_02, area, budget=NO_BUDGET)
    assert found and found["thickness"] == pytest.approx(0.002, rel=1e-6)
    assert found["cells_across"] == pytest.approx(1.28, abs=0.01) and found["area_share"] > 0.95
    assert found["extra_levels"] == 1 and found["refined_cell"] == pytest.approx(0.2 * 2 / 512)
    assert found["cells_across_refined"] == pytest.approx(2.56, abs=0.01)
    assert found["band"] == pytest.approx(2 * 0.2 * 2 / 512)
    assert found["rough"] is False and found["rough_thickness"] is None
    assert found["levels_wanted"] == 1 and not found["capped"] and found["added_cells"] == found["full_added_cells"]
    # the rocket's fins: 2 mm at a 3.28 mm cell (0.6 cells) also need one level (1.64 mm cells)
    rocket = thin.thin_features(th, 0.42 * 2 / 256, area)
    assert rocket and rocket["extra_levels"] == 1 and rocket["cells_across_refined"] == pytest.approx(1.22, abs=0.01)
    assert not rocket["capped"] and rocket["added_cells"] < thin.CELL_BUDGET
    # a 0.9 mm plate at that cell would need two (0.82 mm cells); 0.4 mm would be rough even then
    th_thin = {**th, "plate": [[t * 0.45, a] for t, a in th["plate"]]}
    two = thin.thin_features(th_thin, 0.42 * 2 / 256, area, budget=NO_BUDGET)
    assert two and two["extra_levels"] == 2 and not two["rough"]
    th_thinner = {**th, "plate": [[t * 0.2, a] for t, a in th["plate"]]}
    rough = thin.thin_features(th_thinner, 0.42 * 2 / 256, area, budget=NO_BUDGET)
    assert rough and rough["extra_levels"] == 2 and rough["rough"] is True
    assert rough["rough_thickness"] == pytest.approx(0.0004, rel=1e-6)
    # a finer mesh (or a shorter reference length) already resolves it
    assert thin.thin_features(th, 0.0005, area) is None
    # a much coarser one leaves it rough even after two extra levels
    coarse = thin.thin_features(th, 0.02, area)
    assert coarse and coarse["rough"] is True and coarse["rough_thickness"] == pytest.approx(0.002, rel=1e-6)
    # nothing measured, or too little of the surface: no decision
    assert thin.thin_features(None, 0.003, area) is None
    assert thin.thin_features({"plate": []}, 0.003, area) is None
    assert thin.thin_features(th, 0.003, None) is None
    assert thin.thin_features(th, 0.003, area * 1000) is None  # the plate would be 0.1 % of the surface


def test_band_cells_estimate_matches_the_corpus():
    """The estimate against the three banded corpus runs (validation/robustness.md, task 013 against 012)."""
    plate = thin.band_cells(0.04, QUICK_CELL_02, 1)
    assert plate == pytest.approx(446_568, rel=0.06)  # 193,325 -> 639,893 cells
    rocket = thin.band_cells(0.01815, 0.42 * 2 / 256, 1)
    assert rocket == pytest.approx(51_706, rel=0.2)  # 86,515 -> 138,221
    beam = thin.band_cells(0.2, 2.0 * 2 / 256, 1)
    assert 15_000 < beam < 30_000  # 98,135 -> 182,744: low by 4x, the band also caught the flange's upper face
    # a second level costs about four times the first; no band, no cells; bad inputs: no cells
    assert 3.5 < thin.band_cells(0.04, QUICK_CELL_02, 2) / plate < 4.5
    assert thin.band_cells(0.04, QUICK_CELL_02, 0) == 0
    assert thin.band_cells(0.0, QUICK_CELL_02, 1) == 0 and thin.band_cells(0.04, 0.0, 1) == 0
    # the budget is scale-free: a geometrically similar part at any size costs the same cells
    assert thin.band_cells(0.04 * 25, QUICK_CELL_02 * 5, 1) == pytest.approx(plate, rel=1e-9)


def test_cell_budget_takes_a_level_away_or_leaves_the_mesh_plain():
    th, area = plate_thickness_m()
    # the corpus plate on the Quick mesh: one level wanted, about 430,000 cells, over the 300,000 budget: plain
    plain = thin.thin_features(th, QUICK_CELL_02, area)
    assert plain and plain["levels_wanted"] == 1 and plain["extra_levels"] == 0 and plain["capped"]
    assert 400_000 < plain["full_added_cells"] < 460_000 and plain["added_cells"] == 0
    assert plain["cell_budget"] == thin.CELL_BUDGET == 300_000
    assert plain["refined_cell"] == plain["surface_cell"] and plain["band"] is None
    assert plain["cells_across_refined"] == plain["cells_across"] and plain["rough"] is False
    # a 0.9 mm plate at the rocket's cell wants two levels (400,000 cells): it gets one (96,000), and is then rough
    th_thin = {**th, "plate": [[t * 0.45, a] for t, a in th["plate"]]}
    one = thin.thin_features(th_thin, 0.42 * 2 / 256, area)
    assert one and one["levels_wanted"] == 2 and one["extra_levels"] == 1 and one["capped"]
    assert one["added_cells"] < thin.CELL_BUDGET < one["full_added_cells"] and one["rough"] is True
    assert one["refined_cell"] == pytest.approx(0.42 * 2 / 512) and one["band"] == pytest.approx(2 * 0.42 * 2 / 512)
    # a tenth of the area (a small fin) gets both levels
    small = {**th_thin, "plate": [[t, a * 0.1] for t, a in th_thin["plate"]]}
    two = thin.thin_features(small, 0.42 * 2 / 256, area * 0.1)
    assert two and two["extra_levels"] == 2 and not two["capped"] and not two["rough"]
    # the strip: as thin as the plate, a fifth of the area, within the budget
    ths, areas = strip_thickness_m()
    strip = thin.thin_features(ths, QUICK_CELL_02, areas)
    assert strip and strip["extra_levels"] == 1 and not strip["capped"]
    assert 70_000 < strip["added_cells"] == strip["full_added_cells"] < 100_000
    # a custom budget
    assert thin.thin_features(th, QUICK_CELL_02, area, budget=500_000)["extra_levels"] == 1


def test_notes_are_plain_words_with_the_numbers():
    ths, areas = strip_thickness_m()
    found = thin.thin_features(ths, QUICK_CELL_02, areas)
    lines = thin.notes(found, "Quick")
    assert len(lines) == 1 and "2 mm thick" in lines[0] and "1.6 mm surface cells" in lines[0]
    assert "0.78 mm cells" in lines[0] and "Quick mesh" in lines[0] and "refines the mesh" in lines[0]
    assert "allows" not in lines[0]
    th, area = plate_thickness_m()
    rough = thin.thin_features(th, 0.02, area)
    lines = thin.notes(rough, "Quick")
    assert len(lines) == 2 and "close to the smallest cells" in lines[1] and "More accurate, slower" in lines[1]
    # the budget left the mesh plain: the note says so, with the numbers, and a caveat line follows
    plain = thin.thin_features(th, QUICK_CELL_02, area)
    lines = thin.notes(plain, "Quick")
    assert len(lines) == 2 and "left as it is" in lines[0] and "300,000" in lines[0]
    assert "about 430,000 cells" in lines[0] and "0.78" not in lines[0]
    assert "was not refined" in lines[1] and "More accurate, slower" in lines[1]
    # one level taken away: said in the first line, no extra caveat (unless rough)
    th_thin = {**th, "plate": [[t * 0.45, a] for t, a in th["plate"]]}
    one = thin.thin_features(th_thin, 0.42 * 2 / 256, area)
    lines = thin.notes(one, "Quick")
    assert "one level less" in lines[0] and "1.6 mm cells" in lines[0] and "about 410,000 cells" in lines[0]
    assert len(lines) == 2 and "close to the smallest cells" in lines[1]
    assert thin.notes(None) == []
    assert thin._cells(426_113) == "430,000" and thin._cells(85_321) == "85,000" and thin._cells(7) == "7"


# --------------------------------------------------------------------------- the case builder


def _geometry(tmp_path, name, size, at):
    p = tmp_path / f"{name}.stl"
    write_binary_stl(p, CUBE_V * list(size) + list(at), CUBE_F)
    r = upload.check_stl(p, "mm", output=tmp_path / f"{name}.body.stl")
    return cb.GeometryInput(stl_path=tmp_path / f"{name}.body.stl", bounds_min=r.bounds_min_m,
                            bounds_max=r.bounds_max_m, thickness=r.thickness, surface_area=r.surface_area_m2)


@pytest.fixture
def plate_geometry(tmp_path):
    """The corpus plate (200 x 100 x 2 mm): thin, but its band is over the cell budget."""
    return _geometry(tmp_path, "plate", (200.0, 100.0, 2.0), (0.0, -50.0, -1.0)), cb.ReferenceValues(area=0.02,
                                                                                                    length=0.2)


@pytest.fixture
def strip_geometry(tmp_path):
    """A 200 x 20 x 2 mm strip: as thin, a fifth of the area, within the budget: it gets the band."""
    return _geometry(tmp_path, "strip", (200.0, 20.0, 2.0), (0.0, -10.0, -1.0)), cb.ReferenceValues(area=0.004,
                                                                                                   length=0.2)


def test_case_parameters_switch_on_the_refinement_band(strip_geometry):
    geom, ref = strip_geometry
    p = cb.case_parameters(geom, cb.FlowConditions(airspeed=20, alpha_deg=0), ref, cb.Preset.QUICK, 4)
    assert p["thin_features"]["thickness"] == pytest.approx(0.002, rel=1e-6) and not p["thin_features"]["capped"]
    # level 9 = 0.2 x 2 / 512 = 0.78 mm cells; the band reaches two of them from the thin triangles
    assert p["thin_refinement"]["level"] == 9 and p["thin_refinement"]["triangles"] is None
    assert p["thin_refinement"]["distance"] == pytest.approx(2 * 0.2 * 2 / 512)
    # the entries only appear once the thin surface has been written (build_case fills the triangle count)
    assert cb.template_values(p)["THIN_GEOMETRY"] == "" and cb.template_values(p)["THIN_REFINEMENT"] == ""
    p["thin_refinement"]["triangles"] = 12
    values = cb.template_values(p)
    assert values["THIN_GEOMETRY"] == 'thinParts { type triSurfaceMesh; file "thin.stl"; }'
    assert values["THIN_REFINEMENT"] == "thinParts { mode distance; levels ((0.0015625 9)); }"
    cb.check_dictionary_safe(values["THIN_GEOMETRY"] + values["THIN_REFINEMENT"])
    # without the thickness block (built-in shapes) nothing changes in the dictionary
    bare = cb.GeometryInput(stl_path=geom.stl_path, bounds_min=geom.bounds_min, bounds_max=geom.bounds_max)
    p2 = cb.case_parameters(bare, cb.FlowConditions(airspeed=20, alpha_deg=0), ref, cb.Preset.QUICK, 4)
    assert p2["thin_features"] is None and p2["thin_refinement"] is None
    assert cb.template_values(p2)["THIN_GEOMETRY"] == "" and cb.template_values(p2)["THIN_REFINEMENT"] == ""
    assert cb._thin_entries({"level": 40, "distance": 0.001, "triangles": 3})[1] == (
        "thinParts { mode distance; levels ((0.001 12)); }")
    with pytest.raises(cb.InputError):
        cb._thin_entries({"level": 10, "distance": float("nan"), "triangles": 3})


def test_case_parameters_leave_the_mesh_plain_over_the_budget(plate_geometry):
    """The corpus plate: thin on the Quick mesh, but the band would add about 430,000 cells (task 014)."""
    geom, ref = plate_geometry
    p = cb.case_parameters(geom, cb.FlowConditions(airspeed=20, alpha_deg=0), ref, cb.Preset.QUICK, 4)
    tf = p["thin_features"]
    assert tf["thickness"] == pytest.approx(0.002, rel=1e-6) and tf["capped"] and tf["extra_levels"] == 0
    assert tf["levels_wanted"] == 1 and tf["full_added_cells"] > thin.CELL_BUDGET and tf["band"] is None
    assert p["thin_refinement"] is None
    assert cb.template_values(p)["THIN_GEOMETRY"] == "" and cb.template_values(p)["THIN_REFINEMENT"] == ""
    # the Standard mesh's 1 mm cells span the plate twice: nothing is thin there, nothing is capped
    p2 = cb.case_parameters(geom, cb.FlowConditions(airspeed=20, alpha_deg=0), ref, cb.Preset.STANDARD, 4)
    assert p2["thin_features"] is None and p2["thin_refinement"] is None


def test_built_case_writes_the_thin_surface_only_when_needed(strip_geometry, plate_geometry, tmp_path):
    geom, ref = strip_geometry
    case = cb.build_case(geom, cb.FlowConditions(airspeed=20, alpha_deg=0), ref, runs_root=tmp_path / "runs")
    text = (case.path / "system" / "snappyHexMeshDict").read_text()
    assert 'file "thin.stl"' in text and "mode distance; levels ((0.0015625 9))" in text and "@@" not in text
    cb.check_dictionary_safe(text)
    thin_stl = case.path / "constant" / "triSurface" / "thin.stl"
    assert thin_stl.is_file()
    rec, _ = cb.orient.read_records(thin_stl)
    assert len(rec) == 4 == case.params["thin_refinement"]["triangles"]  # the two big faces (the edges look across)
    meta = json.loads((case.path / "windlab_case.json").read_text())
    assert meta["thin_refinement"]["triangles"] == 4 and meta["thin_features"]["thickness"] == pytest.approx(0.002)
    bare = cb.GeometryInput(stl_path=geom.stl_path, bounds_min=geom.bounds_min, bounds_max=geom.bounds_max)
    case2 = cb.build_case(bare, cb.FlowConditions(airspeed=20, alpha_deg=0), ref, runs_root=tmp_path / "runs")
    text2 = (case2.path / "system" / "snappyHexMeshDict").read_text()
    assert "thinParts" not in text2 and "@@" not in text2
    assert not (case2.path / "constant" / "triSurface" / "thin.stl").exists()
    # the mesh cache tells the two apart
    assert meshcache.mesh_key(case.path) != meshcache.mesh_key(case2.path)
    # over the budget: the thin parts are recorded, but no band and no thin.stl (the plate's mesh of task 012)
    pgeom, pref = plate_geometry
    case3 = cb.build_case(pgeom, cb.FlowConditions(airspeed=20, alpha_deg=0), pref, runs_root=tmp_path / "runs")
    text3 = (case3.path / "system" / "snappyHexMeshDict").read_text()
    assert "thinParts" not in text3 and "@@" not in text3
    assert not (case3.path / "constant" / "triSurface" / "thin.stl").exists()
    meta3 = json.loads((case3.path / "windlab_case.json").read_text())
    assert meta3["thin_refinement"] is None and meta3["thin_features"]["extra_levels"] == 0
    assert meta3["thin_features"]["full_added_cells"] > 300_000


def test_thin_triangles_exact_and_grown():
    tris = body_with_fins()
    th, _ = measure(tris)
    th_m = upload._scale_thickness(th, 1e-3)
    tris_m = tris * 1e-3
    mask = thin.thin_triangles(tris_m, th_m, 0.003)
    # exact (every triangle was measured): the 8 big fin faces (2 triangles each), nothing of the body
    assert mask.sum() == 16 and not mask[:12].any()
    # a sparse sample: only two of the fin faces measured; the neighbours within the sample spacing are added
    sparse = {**th_m, "rays": 20, "samples": {"index": [12, 24], "t": [0.002, 0.002], "plate": [1, 1]}}
    grown = thin.thin_triangles(tris_m, sparse, 0.003)
    assert grown[12] and grown[24] and grown.sum() > 2
    # nothing thin, bad or missing samples: an empty mask
    assert not thin.thin_triangles(tris_m, th_m, 0.001).any()
    assert not thin.thin_triangles(tris_m, None, 0.003).any()
    assert not thin.thin_triangles(tris_m, {"samples": {"index": [99], "t": [0.001], "plate": [1]}}, 0.003).any()
    assert not thin.thin_triangles(np.zeros((0, 3, 3)), th_m, 0.003).any()


def test_geometry_input_validates_the_extra_fields(plate_geometry):
    geom, _ = plate_geometry
    with pytest.raises(ValueError):
        cb.GeometryInput(stl_path=geom.stl_path, bounds_min=geom.bounds_min, bounds_max=geom.bounds_max,
                         surface_area=0.0)
    with pytest.raises(ValueError):
        cb.GeometryInput(stl_path=geom.stl_path, bounds_min=geom.bounds_min, bounds_max=geom.bounds_max,
                         thickness="2 mm")


# --------------------------------------------------------------------------- through the app


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def test_upload_shows_thin_notes_and_the_run_records_them(env, tmp_path):
    a, c = env
    p = tmp_path / "strip.stl"
    write_binary_stl(p, CUBE_V * [200.0, 20.0, 2.0] + [0.0, -10.0, -1.0], CUBE_F)
    g = c.post("/api/geometry", params={"units": "mm", "name": "strip.stl"}, content=p.read_bytes()).json()
    assert g["usable"]
    tf = g["thin_features"]
    assert tf["quick"]["thickness"] == pytest.approx(0.002, rel=1e-6) and not tf["quick"]["rough"]
    assert tf["quick"]["notes"] and "2 mm thick" in tf["quick"]["notes"][0] and not tf["quick"]["capped"]
    # the Standard mesh has smaller cells (1.0 mm): 2 mm spans two of them and is not thin there
    assert "standard" not in tf
    # the same shape fetched again, turned, or with another reference length keeps / updates the block
    assert c.get(f"/api/geometry/{g['geometry_id']}").json()["thin_features"]["quick"]["thickness"] == pytest.approx(
        0.002, rel=1e-6)
    short = c.post(f"/api/geometry/{g['geometry_id']}/reference", json={"length": 0.02}).json()
    assert short["thin_features"] == {}  # a 0.02 m reference length makes the surface cell 0.16 mm
    c.post(f"/api/geometry/{g['geometry_id']}/reference", json={})
    rid = c.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "done"
    assert s["params"]["thin_features"]["thickness"] == pytest.approx(0.002, rel=1e-6)
    assert s["params"]["thin_refinement"]["level"] == 9 and s["params"]["thin_refinement"]["triangles"] == 4
    assert not any("close to the smallest cells" in w for w in s["warnings"])  # resolved after refinement
    assert not any("not refined" in w for w in s["warnings"])
    md = c.get(f"/api/runs/{rid}/report.md").text
    assert "Thin parts" in md and "refinement band" in md and "allowance" not in md
    case = a.fastapi.state.runs.root / rid
    assert "thinParts { mode distance" in (case / "system" / "snappyHexMeshDict").read_text()
    assert (case / "constant" / "triSurface" / "thin.stl").is_file()


def test_upload_over_the_budget_says_the_mesh_stays_plain(env, tmp_path):
    """The corpus plate (task 014): the Thin parts note, the run's warning and the report all say that the band
    was left out and why, and the case has no band."""
    a, c = env
    p = tmp_path / "plate.stl"
    write_binary_stl(p, CUBE_V * [200.0, 100.0, 2.0] + [0.0, -50.0, -1.0], CUBE_F)
    g = c.post("/api/geometry", params={"units": "mm", "name": "plate.stl"}, content=p.read_bytes()).json()
    assert g["usable"]
    q = g["thin_features"]["quick"]
    assert q["capped"] and q["extra_levels"] == 0 and q["full_added_cells"] > 300_000
    assert len(q["notes"]) == 2 and "left as it is" in q["notes"][0] and "300,000" in q["notes"][0]
    assert "More accurate, slower" in q["notes"][1]
    rid = c.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["status"] == "done"
    assert s["params"]["thin_features"]["extra_levels"] == 0 and s["params"]["thin_refinement"] is None
    assert any("not refined around the thin parts" in w for w in s["warnings"])
    md = c.get(f"/api/runs/{rid}/report.md").text
    assert "Thin parts" in md and "left out" in md and "300,000-cell allowance" in md
    case = a.fastapi.state.runs.root / rid
    assert "thinParts" not in (case / "system" / "snappyHexMeshDict").read_text()
    assert not (case / "constant" / "triSurface" / "thin.stl").exists()


def test_built_in_shapes_never_get_the_refinement(env):
    a, c = env
    w = naca(c).json()
    assert w["thin_features"] == {}
    s = c.post("/api/geometry/builtin", json={"shape": "plate", "height": 0.2, "width": 0.2}).json()
    assert s["thin_features"] == {}  # the built-in plate is 1 cm thick, but built-in shapes are never refined
    rid = c.post("/api/runs", json={"geometry_id": s["geometry_id"], "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    st = c.get(f"/api/runs/{rid}").json()
    assert st["params"]["thin_features"] is None and st["params"]["thin_refinement"] is None
    case = a.fastapi.state.runs.root / rid
    assert "thinParts" not in (case / "system" / "snappyHexMeshDict").read_text()
    assert not (case / "constant" / "triSurface" / "thin.stl").exists()


def test_rough_features_warn_after_the_run():
    found = {"thickness": 0.0005, "thinnest": 0.0005, "area": 0.01, "area_share": 0.3, "surface_cell": 0.003,
             "cells_across": 0.17, "extra_levels": 2, "refined_cell": 0.00075, "cells_across_refined": 0.67,
             "rough": True, "rough_thickness": 0.0005}
    notes = thin_notes({"thin_features": found, "preset": "quick"})
    assert len(notes) == 1 and "0.5 mm thick" in notes[0] and "Quick mesh" in notes[0]
    assert thin_notes({"thin_features": {**found, "rough": False}}) == []
    assert thin_notes({}) == []
    # the band left out by the budget: the caveat follows the run even when nothing is rough
    plain = {**found, "rough": False, "rough_thickness": None, "extra_levels": 0, "levels_wanted": 1, "capped": True,
             "added_cells": 0, "full_added_cells": 426_000, "cell_budget": 300_000, "refined_cell": 0.003}
    notes = thin_notes({"thin_features": plain, "preset": "quick"})
    assert len(notes) == 1 and "not refined around the thin parts" in notes[0]


def test_symmetry_note_only_when_the_lift_is_noticeable():
    params = {"geometry_class": "unknown", "mirror_symmetric": {"x": False, "y": True, "z": True}}
    assert symmetry_notes(params, {"cl": -0.58, "cd": 0.65}, 0.0)  # the rocket before task 013
    assert symmetry_notes(params, {"cl": -0.0137, "cd": 0.0204}, 0.0)  # 67 % of the drag
    assert symmetry_notes(params, {"cl": 0.02, "cd": 0.65}, 0.0) == []  # 3 % of the drag: not worth a note
    assert symmetry_notes(params, {"cl": 0.008, "cd": 0.02}, 0.0) == []  # below 0.01 on the reference area
    assert symmetry_notes(params, {"cl": 0.04, "cd": 0.65}, 0.0)  # 6 %: shown
    assert "More accurate" in symmetry_notes(params, {"cl": 0.04, "cd": 0.65}, 0.0)[0]
