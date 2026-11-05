"""Streamline integrator and viewing windows for the flow slice (windlab.app.flowlines)."""

from __future__ import annotations

import numpy as np
import pytest

from windlab.app import flowlines as fl


def grid_mesh(x0, x1, z0, z1, nx, nz, hole=None):
    """Structured triangulation of a rectangle; ``hole`` = (x0, x1, z0, z1) leaves out the
    triangles whose centre is inside it (a solid body in the slice)."""
    xs, zs = np.linspace(x0, x1, nx + 1), np.linspace(z0, z1, nz + 1)
    X, Z = np.meshgrid(xs, zs)
    pts = np.column_stack([X.ravel(), Z.ravel()])
    tris = []
    for j in range(nz):
        for i in range(nx):
            a = j * (nx + 1) + i
            b, c, d = a + 1, a + nx + 1, a + nx + 2
            tris += [[a, b, d], [a, d, c]]
    tris = np.array(tris)
    if hole is not None:
        cen = pts[tris].mean(axis=1)
        hx0, hx1, hz0, hz1 = hole
        inside = (cen[:, 0] > hx0) & (cen[:, 0] < hx1) & (cen[:, 1] > hz0) & (cen[:, 1] < hz1)
        tris = tris[~inside]
    return pts, tris


def test_rasterize_reproduces_a_linear_field_exactly():
    pts, tris = grid_mesh(0, 2, -0.5, 0.5, 20, 10)
    uw = np.column_stack([1 + pts[:, 0], 3 * pts[:, 1]])  # linear: exact on every triangle
    g = fl.rasterize(pts, tris, uw, [0, 2, -0.5, 0.5], across=40)
    assert g.shape == (20, 40) and g.open.all()
    jj, ii = np.mgrid[0:20, 0:40]
    xc, zc = g.x0 + (ii + 0.5) * g.h, g.z0 + (jj + 0.5) * g.h
    assert np.allclose(g.u, 1 + xc) and np.allclose(g.w, 3 * zc)


def test_rasterize_marks_the_body_as_blocked():
    pts, tris = grid_mesh(-1, 3, -1, 1, 40, 20, hole=(0.0, 1.0, -0.1, 0.1))
    g = fl.rasterize(pts, tris, np.tile([1.0, 0.0], (len(pts), 1)), [-1, 3, -1, 1], across=80)
    jj, ii = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    xc, zc = g.x0 + (ii + 0.5) * g.h, g.z0 + (jj + 0.5) * g.h
    in_body = (xc > 0.05) & (xc < 0.95) & (np.abs(zc) < 0.05)
    assert in_body.any() and not g.open[in_body].any()
    far = (np.abs(zc) > 0.2) | (xc < -0.2) | (xc > 1.2)
    assert g.open[far].all()


def test_uniform_flow_gives_straight_lines_across_the_window():
    pts, tris = grid_mesh(0, 4, -1, 1, 40, 20)
    g = fl.rasterize(pts, tris, np.tile([2.0, 0.0], (len(pts), 1)), [0, 4, -1, 1], across=160)
    seeds = fl.seeds_on_left_edge(g, 10)
    assert len(seeds) == 10
    lines = fl.trace(g, seeds)
    for s, line in zip(seeds, lines, strict=True):
        assert np.allclose(line[:, 1], s[1], atol=1e-9)  # stays at its height
        assert line[-1, 0] > 4 - 2 * g.h  # stops at the right edge, not before
        assert np.all(np.diff(line[:, 0]) > 0)


def test_tilted_flow_follows_the_angle():
    a = np.radians(12)
    pts, tris = grid_mesh(0, 4, -1, 1, 40, 20)
    g = fl.rasterize(pts, tris, np.tile([np.cos(a), np.sin(a)], (len(pts), 1)), [0, 4, -1, 1], across=160)
    line = fl.trace(g, [[0.05, -0.9]])[0]
    slope = np.polyfit(line[:, 0], line[:, 1], 1)[0]
    assert slope == pytest.approx(np.tan(a), rel=1e-6)
    assert line[-1, 1] > 1 - 2 * g.h or line[-1, 0] > 4 - 2 * g.h  # left through the top or right edge


def test_rk2_keeps_a_vortex_line_on_its_circle():
    # solid-body rotation u = (-z, x): streamlines are circles about the origin
    pts, tris = grid_mesh(-1, 1, -1, 1, 40, 40)
    uw = np.column_stack([-pts[:, 1], pts[:, 0]])
    g = fl.rasterize(pts, tris, uw, [-1, 1, -1, 1], across=200)
    line = fl.trace(g, [[0.5, 0.0]], max_steps=int(2 * np.pi * 0.5 / (0.5 * g.h)))[0]
    r = np.hypot(line[:, 0], line[:, 1])
    assert np.max(np.abs(r - 0.5)) < 2e-3  # second-order: a full turn stays on the circle
    ang = np.unwrap(np.arctan2(line[:, 1], line[:, 0]))
    assert ang[-1] - ang[0] == pytest.approx(2 * np.pi, rel=0.02)  # counter-clockwise, one turn


def test_lines_stop_at_the_body_and_where_the_air_stops():
    hole = (1.0, 2.0, -0.2, 0.2)
    pts, tris = grid_mesh(0, 4, -1, 1, 80, 40, hole=hole)
    g = fl.rasterize(pts, tris, np.tile([1.0, 0.0], (len(pts), 1)), [0, 4, -1, 1], across=160)
    hit, miss = fl.trace(g, [[0.05, 0.0], [0.05, 0.6]])
    assert hole[0] - 3 * g.h < hit[-1, 0] < hole[0]  # stopped just in front of the body
    assert miss[-1, 0] > 4 - 2 * g.h
    # a stagnant region: the line ends where the speed drops to (nearly) zero
    uw = np.column_stack([np.clip(2 - pts[:, 0], 0, None), np.zeros(len(pts))])
    g2 = fl.rasterize(pts, tris, uw, [0, 4, -1, 1], across=160)
    line = fl.trace(g2, [[0.05, 0.6]], min_speed=0.05)[0]
    assert 1.8 < line[-1, 0] < 2.0


def test_streamlines_for_a_window_are_rounded_polylines():
    pts, tris = grid_mesh(-1, 3, -1, 1, 40, 20, hole=(0.0, 1.0, -0.06, 0.06))
    uw = np.tile([1.0, 0.0], (len(pts), 1))
    lines = fl.streamlines(pts, tris, uw, fl.closeup_window([0, 0, -0.06], [1, 0, 0.06]), n_seeds=24)
    assert 20 <= len(lines) <= 24  # seeds inside the body or too short are dropped
    assert all(len(line) <= fl.MAX_POINTS_PER_LINE and len(line[0]) == 2 for line in lines)
    xs = [p[0] for line in lines for p in line]
    assert min(xs) >= -0.5 - 1e-9 and max(xs) <= 2.5 + 1e-9


def test_closeup_window_and_clipping():
    w = fl.closeup_window([0.0, -0.3, -0.018], [0.3, 0.3, 0.018])
    assert w == pytest.approx([-0.15, 0.75, -0.198, 0.198])
    assert fl.clip_window(w, [-6, 12, -6, 6]) == pytest.approx(w)
    assert fl.clip_window(w, [-0.1, 0.5, -1, 1]) == pytest.approx([-0.1, 0.5, -0.198, 0.198])
    assert fl.clip_window(w, [5, 6, 5, 6]) == [5, 6, 5, 6]  # no overlap: the whole extent
