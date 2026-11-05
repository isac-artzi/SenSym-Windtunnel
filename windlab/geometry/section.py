"""Mid-span cross-section of a shape, for the instant 2D preview in the browser (PRD Tier 0).

The preview simulates the flow around the outline of the part in the plane through the
middle of its span (y = centre of its bounding box, nudged a hair off that plane so the cut
never runs exactly through mesh vertices). Only WindLab's own ``body.stl`` files are read.

* ``naca_outline(code, chord)``: the section from the NACA formula (what the page also
  computes in JavaScript; this is the reference the tests compare with).
* ``slice_stl(path)`` / ``slice_mesh(tris, y)``: cut a closed triangulated surface with the
  plane y = y0 and chain the cut edges into closed loops in the (x, z) plane.
* ``simplify(loop, tol)``: Douglas-Peucker thinning, so a 10-million-triangle upload still
  gives a few hundred points.
* ``section_for_geometry(meta, stl_path)``: the JSON the ``/api/geometry/{id}/section``
  route returns: loops, bounds, the plane's y and the reference length.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from windlab.geometry.naca import naca4_section
from windlab.geometry.stl import _STL_DTYPE

MAX_POINTS_PER_LOOP = 400
MAX_LOOPS = 20
_CHUNK = 1_000_000  # triangles per slicing batch (bounds memory for big uploads)


class SectionError(ValueError):
    """The shape has no usable section (plain-language message)."""


def naca_outline(code: str, chord: float, n_per_side: int = 80) -> np.ndarray:
    """The NACA 4-digit section scaled to ``chord`` metres: (N, 2) points (x, z), counter-clockwise."""
    return naca4_section(code, n_per_side) * float(chord)


# --------------------------------------------------------------------------- slicing


def _segments(tris: np.ndarray, y0: float) -> np.ndarray:
    """Cut segments (M, 2, 2) in (x, z) of the triangles crossing the plane y = y0."""
    d = tris[:, :, 1] - y0
    pos = d > 0
    crossing = pos.any(axis=1) & (~pos).any(axis=1)
    if not crossing.any():
        return np.zeros((0, 2, 2))
    t3, d3, p3 = tris[crossing], d[crossing], pos[crossing]
    pts = np.full((len(t3), 3, 2), np.nan)
    hit = np.zeros((len(t3), 3), bool)
    for k, (a, b) in enumerate(((0, 1), (1, 2), (2, 0))):
        m = p3[:, a] != p3[:, b]
        da, db = d3[m, a], d3[m, b]
        t = da / (da - db)
        P = t3[m, a] + t[:, None] * (t3[m, b] - t3[m, a])
        pts[m, k] = P[:, [0, 2]]
        hit[m, k] = True
    # exactly two edges of each crossing triangle cross the plane: bring them first
    order = np.argsort(~hit, axis=1, kind="stable")[:, :2]
    seg = np.take_along_axis(pts, order[:, :, None], axis=1)
    return seg[np.isfinite(seg).all(axis=(1, 2))]


def _chain(seg: np.ndarray, tol: float) -> list[np.ndarray]:
    """Join segments end to end into closed loops (endpoints matched within ``tol``)."""
    if not len(seg):
        return []
    keys = np.round(seg / tol).astype(np.int64)  # (M, 2, 2)
    ends: dict[tuple[int, int], list[int]] = {}
    for i, (a, b) in enumerate(keys):
        ends.setdefault((int(a[0]), int(a[1])), []).append(i)
        ends.setdefault((int(b[0]), int(b[1])), []).append(i)
    used = np.zeros(len(seg), bool)
    loops = []
    for start in range(len(seg)):
        if used[start]:
            continue
        used[start] = True
        pts = [seg[start, 0], seg[start, 1]]
        key = (int(keys[start, 1, 0]), int(keys[start, 1, 1]))
        first = (int(keys[start, 0, 0]), int(keys[start, 0, 1]))
        while key != first:
            nxt = next((j for j in ends.get(key, ()) if not used[j]), None)
            if nxt is None:
                break  # an open chain (never for a closed surface); keep what we have
            used[nxt] = True
            a, b = keys[nxt]
            if (int(a[0]), int(a[1])) == key:
                pts.append(seg[nxt, 1])
                key = (int(b[0]), int(b[1]))
            else:
                pts.append(seg[nxt, 0])
                key = (int(a[0]), int(a[1]))
        loop = np.asarray(pts[:-1] if key == first else pts)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def _area(loop: np.ndarray) -> float:
    x, z = loop[:, 0], loop[:, 1]
    return 0.5 * float(np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1)))


def simplify(loop: np.ndarray, tol: float, max_points: int = MAX_POINTS_PER_LOOP) -> np.ndarray:
    """Douglas-Peucker on a closed loop: drop points that lie within ``tol`` of the line between
    their kept neighbours; keep tightening until at most ``max_points`` remain."""
    pts = np.asarray(loop, float)
    if len(pts) <= 3:
        return pts
    # split the loop at its two farthest-apart points so each half is an open polyline
    far = int(np.argmax(np.linalg.norm(pts - pts[0], axis=1)))
    pieces = [pts[: far + 1], np.vstack([pts[far:], pts[:1]])]
    for _ in range(40):
        keep = [_dp(p, tol) for p in pieces]
        out = np.vstack([keep[0][:-1], keep[1][:-1]])
        if len(out) <= max_points:
            return out
        tol *= 1.5
    return out


def _dp(pts: np.ndarray, tol: float) -> np.ndarray:
    n = len(pts)
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[a:b + 1]
        d = seg[-1] - seg[0]
        L = np.hypot(*d)
        dist = (np.abs(d[0] * (seg[:, 1] - seg[0, 1]) - d[1] * (seg[:, 0] - seg[0, 0])) / L if L > 0
                else np.linalg.norm(seg - seg[0], axis=1))
        i = int(np.argmax(dist))
        if dist[i] > tol:
            keep[a + i] = True
            stack.append((a, a + i))
            stack.append((a + i, b))
    return pts[keep]


def slice_mesh(tris: np.ndarray, y0: float, tol: float | None = None) -> list[np.ndarray]:
    """Closed loops (x, z), counter-clockwise, of the surface ``tris`` (N, 3, 3) cut at y = y0."""
    tris = np.asarray(tris, float)
    if tol is None:
        ext = float(np.max(tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0))) if len(tris) else 1.0
        tol = max(ext, 1e-9) * 1e-7
    segs = [_segments(tris[i:i + _CHUNK], y0) for i in range(0, len(tris), _CHUNK)]
    seg = np.vstack(segs) if segs else np.zeros((0, 2, 2))
    loops = _chain(seg, tol)
    out = []
    for lp in loops:
        if abs(_area(lp)) <= tol * tol:
            continue
        out.append(lp if _area(lp) > 0 else lp[::-1])
    out.sort(key=lambda lp: -abs(_area(lp)))
    return out[:MAX_LOOPS]


def read_stl_triangles(path: str | os.PathLike) -> np.ndarray:
    """Triangles (N, 3, 3) of a WindLab-written binary STL (metres)."""
    data = Path(path).read_bytes()
    n = int.from_bytes(data[80:84], "little") if len(data) >= 84 else 0
    if n == 0 or len(data) != 84 + 50 * n:
        raise SectionError("The shape's geometry file is damaged; please make or upload the shape again.")
    rec = np.frombuffer(data, dtype=_STL_DTYPE, count=n, offset=84)
    return np.stack([rec["v0"], rec["v1"], rec["v2"]], axis=1).astype(np.float64)


def slice_stl(path: str | os.PathLike, y: float | None = None) -> tuple[list[np.ndarray], float]:
    """Loops of the STL cut at ``y`` (default: the middle of its span, nudged off the vertices).
    Returns (loops, y used)."""
    tris = read_stl_triangles(path)
    lo, hi = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    size = float(np.max(hi - lo)) or 1.0
    y0 = float(0.5 * (lo[1] + hi[1]) + 1e-6 * size) if y is None else float(y)
    loops = slice_mesh(tris, y0, tol=size * 1e-7)
    tol = size * 2e-4
    return [simplify(lp, tol) for lp in loops], y0


# --------------------------------------------------------------------------- for the API


def section_for_geometry(meta: dict, stl_path: Path | None) -> dict:
    """The mid-span outline of a stored geometry as plain JSON for the preview.

    NACA wings come from the formula (chord-scaled, at the root); built-in bodies and
    uploads from a cut through their STL."""
    kind = meta.get("kind")
    if kind == "naca" and meta.get("naca"):
        n = meta["naca"]
        loops = [naca_outline(str(n["code"]), float(n["chord"]))]
        y0, source = 0.0, "formula"
    else:
        if stl_path is None:
            raise SectionError("This shape has no geometry to cut.")
        loops, y0 = slice_stl(stl_path)
        source = "stl"
    if not loops:
        raise SectionError("The plane through the middle of the part does not cut it, so there is no section to "
                           "preview. Turn the part (Step 1) so its longest side goes across the wind.")
    allpts = np.vstack(loops)
    ref = meta.get("reference") or {}
    return {
        "geometry_id": meta.get("id"),
        "kind": kind,
        "name": meta.get("name"),
        "source": source,
        "y": y0,
        "loops": [[[round(float(x), 6), round(float(z), 6)] for x, z in lp] for lp in loops],
        "x_min": float(allpts[:, 0].min()), "x_max": float(allpts[:, 0].max()),
        "z_min": float(allpts[:, 1].min()), "z_max": float(allpts[:, 1].max()),
        "reference_length": float(ref.get("length") or (allpts[:, 0].max() - allpts[:, 0].min())),
    }


__all__ = ["MAX_POINTS_PER_LOOP", "SectionError", "naca_outline", "read_stl_triangles", "section_for_geometry",
           "simplify", "slice_mesh", "slice_stl"]
