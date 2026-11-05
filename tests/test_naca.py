import numpy as np
import pytest
import trimesh

from windlab.geometry.naca import NacaCode, naca4_section, naca_wing, section_area

# Exact area of a unit-chord closed-TE NACA 4-digit section: 10 t * integral of the polynomial.
_POLY_INTEGRAL = 0.2969 * 2 / 3 - 0.1260 / 2 - 0.3516 / 3 + 0.2843 / 4 - 0.1036 / 5


def _mesh(w):
    return trimesh.Trimesh(w.vertices, w.faces, process=False)


def test_parse_code():
    assert NacaCode.parse("0012") == NacaCode(0.0, 0.0, 0.12)
    assert NacaCode.parse("4412") == NacaCode(0.04, 0.4, 0.12)


@pytest.mark.parametrize("bad", ["012", "00120", "abcd", "4012", "0000", "0050", 12])
def test_parse_rejects_bad_codes(bad):
    with pytest.raises(ValueError):
        NacaCode.parse(bad)


def test_section_shape_0012():
    pts = naca4_section("0012", 100)
    assert len(pts) == 200
    # TE first, LE once, closed trailing edge
    assert np.allclose(pts[0], [1.0, 0.0])
    assert np.sum(np.all(np.isclose(pts, [0.0, 0.0]), axis=1)) == 1
    # symmetric, max thickness 12 % near 30 % chord
    upper = pts[pts[:, 1] > 0]
    i = np.argmax(upper[:, 1])
    assert upper[i, 1] * 2 == pytest.approx(0.12, rel=2e-3)
    assert 0.25 < upper[i, 0] < 0.35
    assert section_area("0012") == pytest.approx(10 * 0.12 * _POLY_INTEGRAL, rel=1e-3)


def test_section_counter_clockwise():
    pts = naca4_section("2412", 60)
    x, z = pts[:, 0], pts[:, 1]
    assert 0.5 * (np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1))) > 0


def test_cambered_section_has_lift_side():
    pts = naca4_section("4412", 100)
    # mean line at 40 % chord is 4 % above the chord line
    near = pts[np.abs(pts[:, 0] - 0.4) < 0.02]
    assert near[:, 1].mean() == pytest.approx(0.04, abs=0.005)


def test_rectangular_wing_is_watertight_and_sized():
    w = naca_wing("0012", chord=0.3, span=0.6)
    m = _mesh(w)
    assert m.is_watertight
    assert m.is_winding_consistent
    assert m.euler_number == 2  # closed surface of genus 0
    assert m.volume > 0  # outward normals
    expected = 10 * 0.12 * _POLY_INTEGRAL * 0.3**2 * 0.6
    assert m.volume == pytest.approx(expected, rel=2e-3)
    lo, hi = w.bounds
    assert np.allclose(lo[:2], [0.0, -0.3]) and np.allclose(hi[:2], [0.3, 0.3])
    assert hi[2] - lo[2] == pytest.approx(0.036, rel=2e-3)
    assert w.reference_area == pytest.approx(0.18)
    assert w.mean_aero_chord == pytest.approx(0.3)
    assert w.quarter_chord_point == pytest.approx((0.075, 0.0, 0.0))


def test_outward_normals_point_away_from_centroid():
    w = naca_wing("0012", chord=0.3, span=0.6)
    m = _mesh(w)
    centre = w.vertices.mean(axis=0)
    out = np.einsum("ij,ij->i", m.face_normals, m.triangles_center - centre)
    assert (out > 0).mean() > 0.99


@pytest.mark.parametrize("code,taper", [("4412", 0.5), ("6409", 0.2), ("2440", 1.0), ("0006", 0.7)])
def test_tapered_and_cambered_wings_are_watertight(code, taper):
    w = naca_wing(code, chord=0.5, span=2.0, taper=taper)
    m = _mesh(w)
    assert m.is_watertight and m.is_winding_consistent and m.volume > 0
    assert w.tip_chord == pytest.approx(0.5 * taper)
    # Trapezoidal planform: sections scale with c^2, so volume = A * span * (1 + l + l^2) / 3 * c^2
    area = section_area(code, 100)
    expected = area * 0.5**2 * 2.0 * (1 + taper + taper**2) / 3
    assert m.volume == pytest.approx(expected, rel=5e-3)


def test_tapered_wing_keeps_quarter_chord_straight():
    w = naca_wing("0012", chord=1.0, span=4.0, taper=0.5)
    tip = w.vertices[np.isclose(w.vertices[:, 1], 2.0)]
    assert tip[:, 0].min() == pytest.approx(0.25 - 0.125)
    assert tip[:, 0].max() == pytest.approx(0.25 + 0.375)
    assert w.reference_area == pytest.approx(3.0)
    assert w.mean_aero_chord == pytest.approx(2 / 3 * (1 + 0.5 + 0.25) / 1.5)


@pytest.mark.parametrize("kwargs", [{"chord": 0}, {"span": -1}, {"taper": 1.5}, {"taper": 0.05}])
def test_rejects_bad_dimensions(kwargs):
    with pytest.raises(ValueError):
        naca_wing("0012", **kwargs)


def test_stl_roundtrip(tmp_path):
    w = naca_wing("2412", chord=0.3, span=0.6, taper=0.6)
    path = tmp_path / "wing.stl"
    w.write_stl(path)
    assert path.stat().st_size == 84 + 50 * len(w.faces)
    m = trimesh.load_mesh(path, file_type="stl")  # merges duplicate float32 vertices
    assert m.is_watertight and m.is_winding_consistent
    assert m.volume == pytest.approx(_mesh(w).volume, rel=1e-4)


def test_binary_stl_header_does_not_look_ascii(tmp_path):
    # OpenFOAM (and others) parse files starting with "solid" as ASCII STL.
    path = tmp_path / "w.stl"
    naca_wing().write_stl(path)
    assert not path.read_bytes()[:5].lower().startswith(b"solid")
