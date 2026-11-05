"""Default reference values for an uploaded part: the engineer's convention, measured from the surface.

Engineers quote the coefficients of a **body** (a rocket, a car, a sphere, a bracket) on its
**frontal area**, the silhouette seen from the front, straight into the wind; a **wing** or
blade is quoted on its **planform area**, the outline seen from above. WindLab guesses which
of the two a part is from its proportions (:func:`part_kind`) and lets the student say
otherwise in Step 1.

The areas are the real projected areas of the triangulated surface, not the bounding box:
:func:`silhouette_area` rasterises the triangles onto the plane perpendicular to the wind (or
to z) and counts the pixels any triangle covers, so a rocket's frontal area is the tube plus
the fins, not the 120 x 120 mm box around them. The grid has about 1000 pixels along the
longer side (fewer for very large surfaces), so the error is a fraction of a percent; the box
is used instead - and said so - only for surfaces with more triangles than the limit below.

The reference length stays the part's length along the wind (x) in both cases: it sets the
Reynolds number and the size of the virtual tunnel and mesh, and it is what a student can
read off the part directly.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from windlab.geometry.orient import read_records

RESOLUTION = 1024  # pixels along the longer side of the silhouette's box (small surfaces)
MAX_SILHOUETTE_TRIANGLES = 2_000_000  # above this the box is used (memory and time stay bounded)
CANDIDATE_BUDGET = 2_000_000  # pixel tests per numpy batch
WING_THINNESS = 0.25  # wing-like: height < this fraction of min(length, width) ...
WING_WIDE = 1.5  # ... or wider across the wind than long by this factor ...
WING_AREA_RATIO = 5.0  # ... with a planform area at least this many times the frontal area

KINDS = ("wing", "body")
PROJECTION = {"body": 0, "wing": 2}  # body: along the wind (y-z silhouette); wing: from above (x-y outline)


def part_kind(size, frontal: float | None = None, planform: float | None = None) -> str:
    """"wing" when the part is thin in z compared with its length and width - or, when the two
    silhouette areas are known, when it is wide across the wind and flat (planform area at least
    five times the frontal area, like a wing with a winglet) - else "body"."""
    lx, ly, lz = (float(v) for v in size)
    if lz < WING_THINNESS * min(lx, ly):
        return "wing"
    if frontal and planform and ly > WING_WIDE * lx and planform >= WING_AREA_RATIO * frontal:
        return "wing"
    return "body"


def read_triangles(stl_path: str | Path) -> np.ndarray:
    """The (N, 3, 3) float32 corners of a WindLab binary STL."""
    rec, _ = read_records(Path(stl_path))
    return np.stack([rec["v0"], rec["v1"], rec["v2"]], axis=1)


def resolution_for(n_triangles: int) -> int:
    """Fewer pixels for big surfaces: their triangles are small, so the shadow's edge is sharp anyway."""
    return RESOLUTION if n_triangles <= 100_000 else 768 if n_triangles <= 500_000 else 512


def silhouette_area(tris: np.ndarray, axis: int, resolution: int | None = None) -> float:
    """Area (in the units of ``tris``) of the shadow the triangles cast along ``axis`` (0 = x, 1 = y, 2 = z).

    Rasterised on a grid of ``resolution`` pixels along the longer side of the shadow's box: a pixel
    counts when its centre lies inside any triangle, or when a triangle smaller than a pixel sits in
    it. Overlapping parts of the surface count once, as a shadow does. Accuracy: about half a pixel
    along every edge of the shadow, i.e. a fraction of a percent for a compact shape and about 1 %
    for a very thin one."""
    tris = np.asarray(tris)
    if tris.ndim != 3 or tris.shape[1:] != (3, 3) or len(tris) == 0:
        return 0.0
    resolution = resolution or resolution_for(len(tris))
    keep = [i for i in range(3) if i != axis]
    lo = tris[:, :, keep].reshape(-1, 2).min(axis=0).astype(np.float64)
    hi = tris[:, :, keep].reshape(-1, 2).max(axis=0).astype(np.float64)
    ext = hi - lo
    if not np.all(np.isfinite(ext)) or ext.max() <= 0:
        return 0.0
    # square pixels: about `resolution` along the longer side, at least 64 across the shorter one (a thin plate
    # seen edge-on keeps its area to about 1 %), never more than about 4 million pixels in all
    h = float(ext.max()) / resolution
    if ext.min() > 0:
        h = min(h, float(ext.min()) / 64)
    h = max(h, math.sqrt(float(ext[0] * ext[1]) / 4e6))
    nu, nv = (int(math.ceil(e / h)) + 1 for e in ext)
    grid = np.zeros((nv, nu), dtype=bool)
    chunk = 500_000
    for s in range(0, len(tris), chunk):
        p = (tris[s:s + chunk][:, :, keep].astype(np.float64) - lo) / h  # pixel units (N, 3, 2)
        bmin = np.clip(np.floor(p.min(axis=1)).astype(np.int64), 0, [nu - 1, nv - 1])
        bmax = np.clip(np.floor(p.max(axis=1)).astype(np.int64), 0, [nu - 1, nv - 1])
        span = bmax - bmin + 1
        npx = span[:, 0] * span[:, 1]
        tiny = npx == 1
        grid[bmin[tiny, 1], bmin[tiny, 0]] = True
        big = np.flatnonzero(~tiny)
        if not len(big):
            continue
        cum = np.cumsum(npx[big])
        start = 0
        while start < len(big):  # batches of triangles whose candidate pixels fit the budget
            before = cum[start - 1] if start else 0
            end = max(int(np.searchsorted(cum, before + CANDIDATE_BUDGET, side="right")), start + 1)
            sel = big[start:end]
            _fill(grid, p[sel], bmin[sel], span[sel], npx[sel])
            start = end
    return float(grid.sum()) * h * h


def _fill(grid: np.ndarray, p: np.ndarray, bmin: np.ndarray, span: np.ndarray, npx: np.ndarray) -> None:
    """Mark the pixels whose centres lie inside the triangles ``p`` (pixel units)."""
    total = int(npx.sum())
    tri = np.repeat(np.arange(len(p)), npx)
    k = np.arange(total) - np.repeat(np.cumsum(npx) - npx, npx)  # 0 .. npx-1 within each triangle
    sx = span[tri, 0]
    ix = bmin[tri, 0] + k % sx
    iy = bmin[tri, 1] + k // sx
    cx, cy = ix + 0.5, iy + 0.5
    a, b, c = p[tri, 0], p[tri, 1], p[tri, 2]
    e0 = (b[:, 0] - a[:, 0]) * (cy - a[:, 1]) - (b[:, 1] - a[:, 1]) * (cx - a[:, 0])
    e1 = (c[:, 0] - b[:, 0]) * (cy - b[:, 1]) - (c[:, 1] - b[:, 1]) * (cx - b[:, 0])
    e2 = (a[:, 0] - c[:, 0]) * (cy - c[:, 1]) - (a[:, 1] - c[:, 1]) * (cx - c[:, 0])
    eps = 1e-9
    inside = ((e0 >= -eps) & (e1 >= -eps) & (e2 >= -eps)) | ((e0 <= eps) & (e1 <= eps) & (e2 <= eps))
    grid[iy[inside], ix[inside]] = True


def box_areas(size) -> dict:
    lx, ly, lz = (float(v) for v in size)
    return {"frontal": ly * lz, "planform": lx * ly}


def measure(bounds_min, bounds_max, stl_path: str | Path | None, triangles: int | None = None) -> dict:
    """Both reference areas of a part and WindLab's guess of its kind.

    Returns ``{"frontal", "planform", "method", "detected"}``: the two silhouette areas measured from
    the STL ("silhouette"), or the bounding-box areas ("box") when no file is given, the surface is
    too big, or the file cannot be read."""
    lo, hi = (np.asarray(v, dtype=float) for v in (bounds_min, bounds_max))
    size = hi - lo
    areas, method = None, "box"
    if stl_path is not None and (triangles is None or triangles <= MAX_SILHOUETTE_TRIANGLES):
        try:
            tris = read_triangles(stl_path)
            if 0 < len(tris) <= MAX_SILHOUETTE_TRIANGLES:
                res = resolution_for(len(tris))
                areas = {"frontal": silhouette_area(tris, PROJECTION["body"], res),
                         "planform": silhouette_area(tris, PROJECTION["wing"], res)}
                method = "silhouette"
        except (OSError, ValueError):
            areas = None
    if areas is None or not (areas["frontal"] > 0 and areas["planform"] > 0):
        areas, method = box_areas(size), "box"
    return {**areas, "method": method, "detected": part_kind(size, areas["frontal"], areas["planform"])}


def how_text(kind: str, method: str) -> str:
    """The plain-language convention line shown next to the reference values (and kept in runs, CSV and
    reports). It keeps the words 'frontal area' / 'planform area' that Compare keys on."""
    if kind == "body":
        area = ("frontal area (the silhouette seen from the front, measured from the surface)" if method == "silhouette"
                else "frontal area (the box around the part seen from the front: width x height)")
    else:
        area = ("planform area (the outline seen from above, measured from the surface)" if method == "silhouette"
                else "planform area (the box around the part seen from above: length x width)")
    return f"{area} and length along the wind (x)"


def reference_values(bounds_min, bounds_max, areas: dict, kind: str | None = None) -> dict:
    """Reference values for an uploaded part (x = wind direction, z = up) from :func:`measure`'s areas.

    ``kind`` is the student's choice ("wing" | "body") or None for WindLab's guess. Length: the
    part's length along the wind. Area: the frontal silhouette (body) or the planform outline
    (wing). Moment point: a quarter of the length behind the front, at the middle of the box (the
    quarter-chord of a wing; a conventional point for a body)."""
    lo, hi = (np.asarray(v, dtype=float) for v in (bounds_min, bounds_max))
    size = hi - lo
    used = kind if kind in KINDS else areas.get("detected") or part_kind(size)
    method = areas.get("method", "box")
    length = float(size[0])
    return {
        "length": length,
        "area": float(areas["frontal" if used == "body" else "planform"]),
        "moment_point": [float(lo[0] + 0.25 * length), float(0.5 * (lo[1] + hi[1])), float(0.5 * (lo[2] + hi[2]))],
        "how": how_text(used, method),
        "part_type": used,
        "area_method": method,
    }


def upload_reference(bounds_min, bounds_max, stl_path: str | Path | None, kind: str | None = None,
                     triangles: int | None = None) -> dict:
    """:func:`measure` followed by :func:`reference_values` (one call for callers that keep nothing)."""
    return reference_values(bounds_min, bounds_max, measure(bounds_min, bounds_max, stl_path, triangles), kind)


__all__ = ["KINDS", "MAX_SILHOUETTE_TRIANGLES", "RESOLUTION", "WING_AREA_RATIO", "WING_THINNESS", "WING_WIDE",
           "box_areas", "how_text", "measure", "part_kind", "read_triangles", "reference_values", "resolution_for",
           "silhouette_area", "upload_reference"]
