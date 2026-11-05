"""Mid-span section extraction for the instant 2D preview (windlab.geometry.section)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import make_client
from windlab.app import Settings, create_app
from windlab.geometry import section
from windlab.geometry.naca import naca4_section, naca_wing
from windlab.geometry.shapes import cube, plate, sphere, teardrop
from windlab.geometry.stl import write_binary_stl


def _dist_to_polygon(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Distance of each point to the closed polygon's edges."""
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    out = np.empty(len(pts))
    for k, p in enumerate(pts):
        t = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-30), 0, 1)
        out[k] = np.min(np.linalg.norm(a + t[:, None] * ab - p, axis=1))
    return out


def test_sphere_section_is_a_circle():
    b = sphere(0.2)
    loops = section.slice_mesh(b.vertices[b.faces], 1e-7)
    assert len(loops) == 1
    r = np.linalg.norm(loops[0] - [0.1, 0.0], axis=1)
    assert r.min() > 0.0995 and r.max() < 0.1001
    assert section._area(loops[0]) > 0  # counter-clockwise


def test_plate_and_cube_sections_are_rectangles():
    for body, w, h in ((plate(0.2, 0.3), 0.01, 0.2), (cube(0.25), 0.25, 0.25)):
        loops = section.slice_mesh(body.vertices[body.faces], 1e-7)
        assert len(loops) == 1
        lp = loops[0]
        assert lp[:, 0].min() == pytest.approx(0) and lp[:, 0].max() == pytest.approx(w)
        assert lp[:, 1].min() == pytest.approx(-h / 2) and lp[:, 1].max() == pytest.approx(h / 2)
        assert abs(section._area(lp)) == pytest.approx(w * h, rel=1e-6)


def test_teardrop_section_matches_its_profile():
    b = teardrop(0.2, fineness=3.0)
    loops = section.slice_mesh(b.vertices[b.faces], 1e-7)
    lp = loops[0]
    assert lp[:, 0].min() == pytest.approx(0, abs=1e-6) and lp[:, 0].max() == pytest.approx(0.6, abs=1e-6)
    assert lp[:, 1].max() == pytest.approx(0.1, abs=1e-3) and lp[:, 1].min() == pytest.approx(-0.1, abs=1e-3)


def test_wing_slice_matches_the_naca_formula(tmp_path):
    w = naca_wing("2412", chord=0.3, span=0.6)
    p = tmp_path / "wing.stl"
    w.write_stl(p)
    loops, y0 = section.slice_stl(p)
    assert len(loops) == 1 and abs(y0) < 1e-5
    formula = section.naca_outline("2412", 0.3, n_per_side=200)
    assert _dist_to_polygon(loops[0], formula).max() < 2e-4  # within the simplification tolerance
    assert len(loops[0]) <= section.MAX_POINTS_PER_LOOP


def test_two_separate_bodies_give_two_loops_largest_first(tmp_path):
    a, b = cube(0.2), cube(0.1)
    V = np.vstack([a.vertices, b.vertices + [0.5, 0.0, 0.0]])
    F = np.vstack([a.faces, b.faces + len(a.vertices)])
    p = tmp_path / "two.stl"
    write_binary_stl(p, V, F)
    loops, _ = section.slice_stl(p)
    assert len(loops) == 2 and abs(section._area(loops[0])) > abs(section._area(loops[1]))


def test_slice_through_vertices_is_nudged_and_misses_nothing():
    """The plane through the middle of a wing runs through a row of vertices; slice_stl nudges it."""
    w = naca_wing("0012", chord=0.3, span=0.6)
    loops = section.slice_mesh(w.vertices[w.faces], 0.0 + 1e-6 * 0.6)
    assert len(loops) == 1 and len(loops[0]) > 100


def test_simplify_keeps_shape_and_reduces_points():
    lp = naca4_section("0012", 400)
    s = section.simplify(lp, 1e-4)
    assert 20 < len(s) < len(lp)
    assert _dist_to_polygon(lp, s).max() < 2e-4


def test_naca_outline_is_the_scaled_formula():
    assert np.allclose(section.naca_outline("0012", 0.5), naca4_section("0012", 80) * 0.5)


def test_section_route(tmp_path):
    client = make_client(create_app(Settings(data_dir=tmp_path / "data")))
    g = client.post("/api/geometry/naca", json={"code": "0012", "chord": 0.3, "span": 0.6}).json()
    s = client.get(f"/api/geometry/{g['geometry_id']}/section").json()
    assert s["source"] == "formula" and s["reference_length"] == pytest.approx(0.3)
    assert s["x_min"] == pytest.approx(0) and s["x_max"] == pytest.approx(0.3) and len(s["loops"]) == 1
    g = client.post("/api/geometry/builtin", json={"shape": "sphere", "diameter": 0.2}).json()
    s = client.get(f"/api/geometry/{g['geometry_id']}/section").json()
    assert s["source"] == "stl" and s["reference_length"] == pytest.approx(0.2)
    assert s["z_max"] == pytest.approx(0.1, abs=1e-3) and len(s["loops"]) == 1
    r = client.get("/api/geometry/00000000-0000-4000-8000-000000000000/section")
    assert r.status_code == 404 and "detail" in r.json()
