"""Streamlines and viewing windows for the mid-span flow slice.

The slice (see :mod:`windlab.app.slice`) is a triangulated cut through the flow at
y = centre of the part, with the velocity at its points. For the results page we

1. pick two windows in the (x, z) plane: a **close-up** around the part (0.5 body
   lengths ahead, 1.5 behind, 0.6 above and below) and the **whole tunnel** (the
   slice's own extent);
2. resample the in-plane velocity (Ux, Uz) on a regular grid of square pixels over each
   window (linear interpolation inside each triangle; pixels no triangle covers are the
   part itself or outside the slice: the "blocked" mask);
3. integrate streamlines from evenly spaced seeds on the window's left edge with a
   second-order Runge-Kutta (midpoint) step along the flow direction, stopping at the
   part, at the window edge, where the air is nearly stopped, or after a step limit.

Everything is numpy; :func:`trace` runs all seed lines at once.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GRID_ACROSS = 480  # pixels across a window (the long side of a 2:1 close-up)
N_SEEDS = 24
MAX_POINTS_PER_LINE = 300


@dataclass
class VelocityGrid:
    """In-plane velocity on square pixels; pixel (j, i) has its centre at
    (x0 + (i + 0.5) h, z0 + (j + 0.5) h)."""

    x0: float
    z0: float
    h: float
    u: np.ndarray  # (nz, nx)
    w: np.ndarray  # (nz, nx)
    open: np.ndarray  # (nz, nx) bool: True where there is air (a triangle covers the pixel centre)

    @property
    def shape(self) -> tuple[int, int]:
        return self.u.shape

    @property
    def x1(self) -> float:
        return self.x0 + self.u.shape[1] * self.h

    @property
    def z1(self) -> float:
        return self.z0 + self.u.shape[0] * self.h


# ----------------------------------------------------------------------------- windows


def closeup_window(body_min, body_max) -> list[float]:
    """[x0, x1, z0, z1] around the part: 0.5 body lengths ahead, 1.5 behind, 0.6 above / below."""
    bx0, bz0 = float(body_min[0]), float(body_min[-1])
    bx1, bz1 = float(body_max[0]), float(body_max[-1])
    length = max(bx1 - bx0, 1e-6)
    return [bx0 - 0.5 * length, bx1 + 1.5 * length, bz0 - 0.6 * length, bz1 + 0.6 * length]


def clip_window(window, extent) -> list[float]:
    """The window, limited to the slice's extent [x0, x1, z0, z1] (a slice cut short by an
    older template is never shown with empty margins)."""
    x0, x1, z0, z1 = window
    ex0, ex1, ez0, ez1 = extent
    out = [max(x0, ex0), min(x1, ex1), max(z0, ez0), min(z1, ez1)]
    if out[1] <= out[0] or out[3] <= out[2]:
        return list(extent)
    return out


# ----------------------------------------------------------------------------- resampling


def rasterize(xz: np.ndarray, tris: np.ndarray, uw: np.ndarray, window, across: int = GRID_ACROSS) -> VelocityGrid:
    """Linear interpolation of point values ``uw`` (n, 2) on the triangles onto square pixels."""
    x0, x1, z0, z1 = (float(v) for v in window)
    h = max(x1 - x0, z1 - z0) / max(8, int(across))
    nx, nz = max(2, int(np.ceil((x1 - x0) / h))), max(2, int(np.ceil((z1 - z0) / h)))
    u = np.zeros((nz, nx))
    w = np.zeros((nz, nx))
    opened = np.zeros((nz, nx), dtype=bool)
    xz = np.asarray(xz, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    uw = np.asarray(uw, dtype=np.float64).reshape(-1, 2)
    if not len(tris):
        return VelocityGrid(x0, z0, h, u, w, opened)
    a, b, c = xz[tris[:, 0]], xz[tris[:, 1]], xz[tris[:, 2]]
    lo, hi = np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)
    # pixel-index range whose centres can fall inside each triangle
    i0 = np.maximum(np.ceil((lo[:, 0] - x0) / h - 0.5), 0).astype(np.int64)
    i1 = np.minimum(np.floor((hi[:, 0] - x0) / h - 0.5), nx - 1).astype(np.int64)
    j0 = np.maximum(np.ceil((lo[:, 1] - z0) / h - 0.5), 0).astype(np.int64)
    j1 = np.minimum(np.floor((hi[:, 1] - z0) / h - 0.5), nz - 1).astype(np.int64)
    ni, nj = np.maximum(i1 - i0 + 1, 0), np.maximum(j1 - j0 + 1, 0)
    count = ni * nj
    keep = count > 0
    tri_idx = np.repeat(np.nonzero(keep)[0], count[keep])
    if not len(tri_idx):
        return VelocityGrid(x0, z0, h, u, w, opened)
    # position of each candidate within its triangle's pixel box
    start = np.cumsum(count[keep]) - count[keep]
    k = np.arange(len(tri_idx)) - np.repeat(start, count[keep])
    ii = i0[tri_idx] + k % ni[tri_idx]
    jj = j0[tri_idx] + k // ni[tri_idx]
    px, pz = x0 + (ii + 0.5) * h, z0 + (jj + 0.5) * h
    # barycentric coordinates
    ax, az = a[tri_idx, 0], a[tri_idx, 1]
    v0x, v0z = b[tri_idx, 0] - ax, b[tri_idx, 1] - az
    v1x, v1z = c[tri_idx, 0] - ax, c[tri_idx, 1] - az
    v2x, v2z = px - ax, pz - az
    den = v0x * v1z - v1x * v0z
    ok = np.abs(den) > 1e-300
    den = np.where(ok, den, 1.0)
    lb = (v2x * v1z - v1x * v2z) / den
    lc = (v0x * v2z - v2x * v0z) / den
    la = 1.0 - lb - lc
    eps = -1e-9
    inside = ok & (la >= eps) & (lb >= eps) & (lc >= eps)
    t = tris[tri_idx[inside]]
    vals = (la[inside, None] * uw[t[:, 0]] + lb[inside, None] * uw[t[:, 1]] + lc[inside, None] * uw[t[:, 2]])
    jj, ii = jj[inside], ii[inside]
    u[jj, ii], w[jj, ii] = vals[:, 0], vals[:, 1]
    opened[jj, ii] = True
    return VelocityGrid(x0, z0, h, u, w, opened)


# ----------------------------------------------------------------------------- integration


def sample(grid: VelocityGrid, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear velocity at points p (n, 2), and whether all four neighbouring pixels are air."""
    nz, nx = grid.shape
    fx = (p[:, 0] - grid.x0) / grid.h - 0.5
    fz = (p[:, 1] - grid.z0) / grid.h - 0.5
    finite = np.isfinite(fx) & np.isfinite(fz)
    fx, fz = np.where(finite, fx, -10.0), np.where(finite, fz, -10.0)
    i, j = np.floor(fx).astype(np.int64), np.floor(fz).astype(np.int64)
    valid = finite & (i >= 0) & (j >= 0) & (i + 1 < nx) & (j + 1 < nz)
    i, j = np.clip(i, 0, nx - 2), np.clip(j, 0, nz - 2)
    tx, tz = fx - i, fz - j
    o = grid.open
    valid &= o[j, i] & o[j, i + 1] & o[j + 1, i] & o[j + 1, i + 1]
    out = np.zeros((len(p), 2))
    for comp, f in ((0, grid.u), (1, grid.w)):
        out[:, comp] = ((1 - tx) * (1 - tz) * f[j, i] + tx * (1 - tz) * f[j, i + 1]
                        + (1 - tx) * tz * f[j + 1, i] + tx * tz * f[j + 1, i + 1])
    return out, valid


def trace(grid: VelocityGrid, seeds, step: float | None = None, max_steps: int | None = None,
          min_speed: float = 1e-3) -> list[np.ndarray]:
    """Streamlines from each seed: RK2 (midpoint) steps of fixed length along the local flow
    direction. A line stops when it would enter the part or leave the grid, where the speed
    drops below ``min_speed`` (same units as the velocity), or after ``max_steps``."""
    seeds = np.atleast_2d(np.asarray(seeds, dtype=np.float64))
    nz, nx = grid.shape
    step = 0.5 * grid.h if step is None else float(step)
    max_steps = 4 * (nx + nz) if max_steps is None else int(max_steps)
    p = seeds.copy()
    lines: list[list[np.ndarray]] = [[q.copy()] for q in p]
    _, alive = sample(grid, p)
    for _ in range(max_steps):
        if not alive.any():
            break
        idx = np.nonzero(alive)[0]
        q = p[idx]
        v1, ok1 = sample(grid, q)
        s1 = np.hypot(v1[:, 0], v1[:, 1])
        ok1 &= s1 > min_speed
        mid = q + 0.5 * step * v1 / np.where(ok1, s1, 1.0)[:, None]
        v2, ok2 = sample(grid, mid)
        s2 = np.hypot(v2[:, 0], v2[:, 1])
        ok = ok1 & ok2 & (s2 > min_speed)
        nxt = q + step * v2 / np.where(ok, s2, 1.0)[:, None]
        _, ok3 = sample(grid, nxt)
        ok &= ok3
        for n in np.nonzero(ok)[0]:
            lines[idx[n]].append(nxt[n].copy())
        p[idx[ok]] = nxt[ok]
        alive[idx[~ok]] = False
    return [np.array(line) for line in lines]


def seeds_on_left_edge(grid: VelocityGrid, n: int = N_SEEDS) -> np.ndarray:
    """``n`` seeds evenly spaced in z just inside the grid's left edge (only where there is air)."""
    nz, _ = grid.shape
    z = grid.z0 + (np.arange(n) + 0.5) * (nz * grid.h) / n
    x = np.full(n, grid.x0 + 0.75 * grid.h)
    p = np.column_stack([x, z])
    _, ok = sample(grid, p)
    return p[ok]


def thin(line: np.ndarray, max_points: int = MAX_POINTS_PER_LINE) -> np.ndarray:
    if len(line) <= max_points:
        return line
    idx = np.linspace(0, len(line) - 1, max_points).round().astype(int)
    return line[np.unique(idx)]


def streamlines(xz, tris, uw, window, n_seeds: int = N_SEEDS, across: int = GRID_ACROSS,
                min_speed: float = 1e-3) -> list[list[list[float]]]:
    """Streamlines for one window, as [[x, z], ...] polylines in metres (rounded)."""
    grid = rasterize(xz, tris, uw, window, across)
    seeds = seeds_on_left_edge(grid, n_seeds)
    if not len(seeds):
        return []
    lines = trace(grid, seeds, min_speed=min_speed)
    digits = max(4, int(np.ceil(-np.log10(grid.h))) + 2)
    return [np.round(thin(line), digits).tolist() for line in lines if len(line) >= 3]
