"""Built-in bluff and streamlined bodies: watertight, outward, sized in metres, sensible reference values."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from windlab.geometry import shapes
from windlab.geometry.shapes import SHAPES, catalogue, make_body


def _mesh(b):
    return trimesh.Trimesh(b.vertices, b.faces, process=False)


@pytest.mark.parametrize("shape", list(SHAPES))
def test_default_shapes_are_watertight_and_outward(shape):
    b = make_body(shape)
    m = _mesh(b)
    assert m.is_watertight and m.is_winding_consistent and m.euler_number == 2, shape
    assert m.volume > 0
    lo, hi = b.bounds
    assert lo[0] == pytest.approx(0.0, abs=1e-9)  # nose at x = 0
    assert abs(lo[1] + hi[1]) < 1e-9 and abs(lo[2] + hi[2]) < 1e-9  # centred on y = z = 0
    assert b.reference_area > 0 and b.reference_length > 0
    assert b.geometry_class in ("bluff", "bluff-smooth", "streamlined-body")
    assert b.geometry_class == {"sphere": "bluff-smooth", "cylinder": "bluff-smooth", "plate": "bluff", "cube": "bluff",
                                "teardrop": "streamlined-body"}[shape]


def test_sphere_volume_and_frontal_area():
    b = shapes.sphere(0.2, n_circ=288)
    assert _mesh(b).volume == pytest.approx(4 / 3 * np.pi * 0.1**3, rel=2e-3)
    assert b.reference_area == pytest.approx(np.pi * 0.1**2) and b.reference_length == 0.2
    assert np.allclose(b.bounds[1] - b.bounds[0], 0.2, atol=1e-9)


def test_cylinder_lies_across_the_wind():
    b = shapes.cylinder(0.15, 0.45)
    size = b.bounds[1] - b.bounds[0]
    assert size[0] == pytest.approx(0.15) and size[1] == pytest.approx(0.45) and size[2] == pytest.approx(0.15)
    assert _mesh(b).volume == pytest.approx(np.pi * 0.075**2 * 0.45, rel=2e-3)
    assert b.reference_area == pytest.approx(0.15 * 0.45) and b.reference_length == 0.15
    with pytest.raises(ValueError, match="times its diameter"):
        shapes.cylinder(0.05, 1.5)


def test_plate_and_cube_are_boxes():
    p = shapes.plate(0.2, 0.3)
    size = p.bounds[1] - p.bounds[0]
    assert size[0] == pytest.approx(0.01) and size[1] == pytest.approx(0.3) and size[2] == pytest.approx(0.2)
    assert p.reference_area == pytest.approx(0.06) and p.reference_length == 0.2
    c = shapes.cube(0.2)
    assert _mesh(c).volume == pytest.approx(0.008) and c.reference_area == pytest.approx(0.04)
    assert len(c.faces) == 12


def test_teardrop_fineness_and_streamlined_class():
    b = shapes.teardrop(0.2, 3.0)
    size = b.bounds[1] - b.bounds[0]
    assert size[0] == pytest.approx(0.6) and size[1] == pytest.approx(0.2, rel=1e-3)
    assert b.geometry_class == "streamlined-body" and b.reference_area == pytest.approx(np.pi * 0.01)
    # the thickest station sits near 30 % of the length, as for the NACA profile
    r = np.hypot(b.vertices[:, 1], b.vertices[:, 2])
    assert 0.25 * 0.6 < b.vertices[np.argmax(r), 0] < 0.35 * 0.6
    with pytest.raises(ValueError, match="fineness"):
        shapes.teardrop(0.2, 1.0)


def test_bad_inputs_have_plain_messages():
    with pytest.raises(ValueError, match="between 0.02 m and 2 m"):
        shapes.sphere(5)
    with pytest.raises(ValueError, match="between"):
        shapes.cube(float("nan"))
    with pytest.raises(ValueError, match="built-in shapes"):
        make_body("torus")
    with pytest.raises(ValueError, match="Unknown setting"):
        make_body("sphere", radius=0.1)


def test_catalogue_is_plain_data():
    cat = catalogue()
    assert [c["id"] for c in cat] == ["sphere", "cylinder", "plate", "cube", "teardrop"]
    for c in cat:
        assert c["description"] and c["class"] in ("bluff", "bluff-smooth", "streamlined-body")
        for p in c["params"]:
            assert p["min"] <= p["default"] <= p["max"]


def test_revolve_rejects_bad_profiles():
    with pytest.raises(ValueError):
        shapes.revolve([0, 1, 2], [0.1, 1, 0])
    with pytest.raises(ValueError):
        shapes.revolve([0, 1, 2], [0, 0, 0])
