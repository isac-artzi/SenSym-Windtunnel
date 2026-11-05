"""Untrusted-STL parser, run as a separate process by :mod:`windlab.geometry.upload`.

Usage: python -I _stl_worker.py <path> <max_triangles> [<out_path> <scale>]

Self-contained on purpose (stdlib + numpy only): it is started with ``python -I`` so it
never imports code from the current or script directory, under CPU / memory / file-size
limits set by the parent. It prints one JSON object on stdout with raw counts in the
file's own units (plus, for a closed surface, the local-thickness samples of
:func:`thickness`); the parent turns them into a plain-language report.

With ``out_path`` and ``scale`` it also writes the cleaned surface as a new binary STL:
coordinates multiplied by ``scale`` (file units -> metres), zero-area triangles dropped,
and the winding flipped if the closed surface is inside-out. The file is created
exclusively (never overwrites) and the parent sets a file-size limit just big enough.
Exit codes: 0 ok, 2 unreadable file (JSON has "error"), 3 out of memory.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import numpy as np

# Self-intersection (surfaces crossing each other) check: skipped above this many triangles,
# and stopped (reported as "timed out") after this many seconds, so the worker always stays
# well inside the CPU / wall-clock limits the parent sets.
SELF_INTERSECTION_MAX_TRIANGLES = 400_000  # about 10 s for 360k triangles on an Apple M-series desktop
SELF_INTERSECTION_SECONDS = 20.0
_PAIR_CHUNK = 100_000  # triangle pairs tested per numpy batch (bounds memory)

_BINARY_DTYPE = np.dtype(
    [("normal", "<f4", 3), ("v0", "<f4", 3), ("v1", "<f4", 3), ("v2", "<f4", 3), ("attr", "<u2")]
)
_VERTEX = re.compile(rb"^\s*vertex\s+(\S+)\s+(\S+)\s+(\S+)", re.M)
_NORMAL = re.compile(rb"^\s*facet\s+normal\s+(\S+)\s+(\S+)\s+(\S+)", re.M)


class BadFile(ValueError):
    pass


def read_stl(data: bytes, max_triangles: int) -> tuple[str, np.ndarray, np.ndarray]:
    """Return (format, triangles (N, 3, 3) float64, stored normals (N, 3) float64)."""
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], "little")
        if len(data) == 84 + 50 * n:
            if n > max_triangles:
                raise BadFile(f"too many triangles ({n:,d}; the limit is {max_triangles:,d})")
            if n == 0:
                raise BadFile("the file contains no triangles")
            rec = np.frombuffer(data, dtype=_BINARY_DTYPE, count=n, offset=84)
            tris = np.stack([rec["v0"], rec["v1"], rec["v2"]], axis=1).astype(np.float64)
            return "binary", tris, rec["normal"].astype(np.float64)
    if data.lstrip()[:5].lower() == b"solid":
        n_facets = data.count(b"endfacet")
        if n_facets > max_triangles:
            raise BadFile(f"too many triangles ({n_facets:,d}; the limit is {max_triangles:,d})")
        verts = _VERTEX.findall(data)
        if not verts:
            raise BadFile("this looks like a text STL file, but it contains no triangles")
        if len(verts) % 3 or len(verts) // 3 != n_facets:
            raise BadFile("the text STL file is damaged (a facet does not have exactly three vertices)")
        try:
            v = np.array(verts, dtype=np.float64)
            normals = _NORMAL.findall(data)
            nrm = np.array(normals, dtype=np.float64) if len(normals) == n_facets else np.zeros((n_facets, 3))
        except ValueError:
            raise BadFile("the text STL file contains a coordinate that is not a number") from None
        return "ascii", v.reshape(-1, 3, 3), nrm
    raise BadFile("this is not an STL file (neither binary STL nor text STL)")


def _count_pieces(n_vertices: int, a: np.ndarray, b: np.ndarray, faces: np.ndarray) -> int:
    """Number of separate shells: connected components of the edge graph (vectorised
    hook-and-pointer-jump, a few dozen numpy passes even for millions of edges)."""
    label = np.arange(n_vertices)
    for _ in range(200):
        la, lb = label[a], label[b]
        if np.array_equal(la, lb):
            break
        lo, hi = np.minimum(la, lb), np.maximum(la, lb)
        np.minimum.at(label, hi, lo)  # hook the larger root under the smaller
        while True:  # pointer jumping until every vertex points at its root
            nxt = label[label]
            if np.array_equal(nxt, label):
                break
            label = nxt
    used = np.unique(faces.reshape(-1))
    return int(len(np.unique(label[used])))


def analyse(tris: np.ndarray, normals: np.ndarray) -> tuple[dict, np.ndarray, np.ndarray, float]:
    """Return (raw counts, mask of non-degenerate triangles, merged corner ids (N, 3), merge tolerance)."""
    if not np.all(np.isfinite(tris)):
        raise BadFile("the file contains coordinates that are infinite or not a number")
    n = len(tris)
    lo, hi = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    diag = float(np.linalg.norm(hi - lo))
    if diag == 0:
        raise BadFile("all points of the part are in the same place (zero size)")

    # Merge coincident corners (tolerance 1e-9 of the part size; CAD exports share exact coordinates).
    tol = diag * 1e-9
    keys = np.round(tris.reshape(-1, 3) / tol).astype(np.int64)
    _, vid = np.unique(keys, axis=0, return_inverse=True)
    del keys
    f = vid.reshape(-1).reshape(n, 3)
    n_vertices = int(vid.max()) + 1

    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area2 = np.linalg.norm(cross, axis=1)
    collapsed = (f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 2] == f[:, 0])
    degenerate = collapsed | (area2 <= (tol * tol))
    surface_area = 0.5 * float(area2.sum())
    signed_volume = float(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum() / 6.0)

    # Stored normals that disagree with the corner order (informational; OpenFOAM uses the order).
    stored = np.linalg.norm(normals, axis=1) > 0
    flipped_normals = int(np.sum(stored & ~degenerate & (np.einsum("ij,ij->i", normals, cross) < 0)))

    # Edge topology on non-degenerate triangles.
    g = f[~degenerate]
    a = np.concatenate([g[:, 0], g[:, 1], g[:, 2]])
    b = np.concatenate([g[:, 1], g[:, 2], g[:, 0]])
    lo_v, hi_v = np.minimum(a, b), np.maximum(a, b)
    forward = (a < b).astype(np.int64)
    key = lo_v * np.int64(n_vertices) + hi_v
    order = np.argsort(key, kind="stable")
    key, forward = key[order], forward[order]
    uniq, start, count = np.unique(key, return_index=True, return_counts=True)
    fwd_sum = np.add.reduceat(forward, start) if len(start) else np.zeros(0, np.int64)
    open_edges = int(np.sum(count == 1))
    nonmanifold_edges = int(np.sum(count > 2))
    # A shared edge must be walked once in each direction by its two triangles.
    inconsistent_edges = int(np.sum((count == 2) & (fwd_sum != 1)))

    pieces = _count_pieces(n_vertices, lo_v, hi_v, g)

    # Mirror symmetry about the three mid-planes of the box: the share of vertices whose mirror image is also
    # a vertex (within 1e-4 of the size). A part that is symmetric top to bottom (z) must have zero lift at
    # 0 degrees; whatever the solver shows there is numerical noise, and the app says so.
    _, first = np.unique(vid, return_index=True)
    mirror = _mirror_match(tris.reshape(-1, 3)[first], lo, hi, diag * 1e-4)

    return {
        "triangles": n,
        "vertices": n_vertices,
        "degenerate_triangles": int(degenerate.sum()),
        "open_edges": open_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "inconsistent_edges": inconsistent_edges,
        "flipped_stored_normals": flipped_normals,
        "pieces": pieces,
        "edges": int(len(uniq)),
        "bounds_min": lo.tolist(),
        "bounds_max": hi.tolist(),
        "surface_area": surface_area,
        "signed_volume": signed_volume,
        "mirror_match": mirror,
    }, ~degenerate, f, tol


def _mirror_match(pts: np.ndarray, lo: np.ndarray, hi: np.ndarray, tol: float) -> list[float]:
    """For each axis, the fraction of (distinct) vertices whose mirror image about the box's mid-plane
    coincides with a vertex, to within ``tol``. 1.0 = perfectly mirror-symmetric."""
    if tol <= 0 or not len(pts):
        return [0.0, 0.0, 0.0]

    def pack(p: np.ndarray) -> np.ndarray:
        k = np.round((p - lo) / tol).astype(np.int64) + 1  # >= 1; coordinates/tol stay far below 2**21
        k = np.clip(k, 0, (1 << 21) - 1)
        return np.unique((k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2])

    have = pack(pts)
    out = []
    for axis in range(3):
        m = pts.copy()
        m[:, axis] = lo[axis] + hi[axis] - m[:, axis]
        want = pack(m)
        out.append(float(np.isin(want, have).mean()) if len(want) else 0.0)
    return out


def _candidate_pairs(tris: np.ndarray, max_entries: int = 6_000_000) -> np.ndarray:
    """Pairs (i < j) of triangles whose bounding boxes share a cell of a uniform grid.

    The cell size starts at twice the median triangle size and grows until the triangle-cell
    table fits in ``max_entries``. Returns an (M, 2) int64 array (no duplicates)."""
    n = len(tris)
    lo, hi = tris.min(axis=1), tris.max(axis=1)  # (n, 3) bounding boxes
    ext = hi - lo
    box_lo, box_hi = lo.min(axis=0), hi.max(axis=0)
    size = np.maximum(box_hi - box_lo, 1e-300)
    h = max(2.0 * float(np.median(ext.max(axis=1))), float(size.max()) / 256)
    for _ in range(40):
        ncell = np.maximum(1, np.minimum(256, np.ceil(size / h))).astype(np.int64)
        c0 = np.clip(np.floor((lo - box_lo) / h), 0, ncell - 1).astype(np.int64)
        c1 = np.clip(np.floor((hi - box_lo) / h), 0, ncell - 1).astype(np.int64)
        per_tri = np.prod(c1 - c0 + 1, axis=1)
        if per_tri.sum() <= max_entries or ncell.max() <= 1:
            break
        h *= 1.5
    # triangle-cell table: every cell each triangle's box touches
    tri_id = np.repeat(np.arange(n), per_tri)
    k = np.arange(len(tri_id)) - np.repeat(np.cumsum(per_tri) - per_tri, per_tri)  # 0 .. per_tri-1
    span = (c1 - c0 + 1)[tri_id]
    ix = c0[tri_id, 0] + k % span[:, 0]
    iy = c0[tri_id, 1] + (k // span[:, 0]) % span[:, 1]
    iz = c0[tri_id, 2] + k // (span[:, 0] * span[:, 1])
    cell = (ix * ncell[1] + iy) * ncell[2] + iz
    order = np.lexsort((tri_id, cell))
    cell, tri_id = cell[order], tri_id[order]
    _, start, count = np.unique(cell, return_index=True, return_counts=True)
    pairs = []
    for d in range(1, int(count.max()) if len(count) else 0):  # partner d places further on in the same cell
        sel = count > d
        if not sel.any():
            break
        s, c = start[sel], count[sel]
        m = c - d
        first = np.repeat(s, m) + (np.arange(m.sum()) - np.repeat(np.cumsum(m) - m, m))
        pairs.append(np.column_stack([tri_id[first], tri_id[first + d]]))
    if not pairs:
        return np.zeros((0, 2), np.int64)
    p = np.vstack(pairs)
    p = np.sort(p, axis=1)
    key = p[:, 0] * np.int64(n) + p[:, 1]
    key = np.unique(key)
    return np.column_stack([key // n, key % n])


def _crossing_interval(T: np.ndarray, d: np.ndarray, D: np.ndarray, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """For each triangle T (m, 3, 3) with signed distances d (m, 3) to the other plane,
    the interval (along direction D) of its intersection segment with that plane."""
    m = len(T)
    smin, smax = np.full(m, np.inf), np.full(m, -np.inf)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = d[:, a], d[:, b]
        valid = (da * db <= 0) & ~((np.abs(da) <= tol) & (np.abs(db) <= tol))
        den = da - db
        t = np.where(np.abs(den) > 0, da / np.where(den == 0, 1.0, den), 0.0)
        t = np.clip(t, 0.0, 1.0)
        P = T[:, a] + t[:, None] * (T[:, b] - T[:, a])
        s = np.einsum("ij,ij->i", P, D)
        smin = np.where(valid, np.minimum(smin, s), smin)
        smax = np.where(valid, np.maximum(smax, s), smax)
    return smin, smax


def _pairs_cross(A: np.ndarray, B: np.ndarray, tol: float) -> np.ndarray:
    """Which pairs of triangles A[k], B[k] (both (m, 3, 3)) cross each other properly.

    Möller-style test: each triangle must have corners strictly on both sides of the
    other's plane (touching, coplanar or merely adjacent triangles never count), and the
    two segments where they cut each other's plane must overlap along the common line."""
    nA = np.cross(A[:, 1] - A[:, 0], A[:, 2] - A[:, 0])
    nB = np.cross(B[:, 1] - B[:, 0], B[:, 2] - B[:, 0])
    # distances scaled by the normal lengths: compare with tol in real length units
    lenA = np.linalg.norm(nA, axis=1)
    lenB = np.linalg.norm(nB, axis=1)
    ok = (lenA > 0) & (lenB > 0)
    nAu = nA / np.where(lenA > 0, lenA, 1.0)[:, None]
    nBu = nB / np.where(lenB > 0, lenB, 1.0)[:, None]
    dA = np.einsum("ikj,ij->ik", A - B[:, :1], nBu)  # A's corners against B's plane
    dB = np.einsum("ikj,ij->ik", B - A[:, :1], nAu)
    strict = (dA > tol).any(axis=1) & (dA < -tol).any(axis=1) & (dB > tol).any(axis=1) & (dB < -tol).any(axis=1)
    ok &= strict
    if not ok.any():
        return ok
    D = np.cross(nAu, nBu)
    lenD = np.linalg.norm(D, axis=1)
    ok &= lenD > 1e-12
    Du = D / np.where(lenD > 0, lenD, 1.0)[:, None]
    a0, a1 = _crossing_interval(A, dA, Du, tol)
    b0, b1 = _crossing_interval(B, dB, Du, tol)
    overlap = np.minimum(a1, b1) - np.maximum(a0, b0)
    return ok & np.isfinite(overlap) & (overlap > tol)


def self_intersections(
    tris: np.ndarray, faces: np.ndarray, keep: np.ndarray, tol: float, *,
    max_triangles: int = SELF_INTERSECTION_MAX_TRIANGLES, seconds: float = SELF_INTERSECTION_SECONDS,
) -> tuple[str, int]:
    """Count pairs of non-adjacent triangles that cross each other.

    Returns (status, count): status is "done", "skipped" (too many triangles) or "timed out"
    (the deadline passed; count is what was found until then). ``faces`` are the merged
    corner ids (adjacent triangles share one), ``keep`` masks out zero-area triangles."""
    idx = np.flatnonzero(keep)
    if len(idx) > max_triangles:
        return "skipped", 0
    if len(idx) < 2:
        return "done", 0
    deadline = time.monotonic() + seconds
    T, F = tris[idx], faces[idx]
    pairs = _candidate_pairs(T)
    if time.monotonic() > deadline:
        return "timed out", 0
    if len(pairs):
        # adjacent triangles (sharing a corner) touch by construction: not a crossing
        fa, fb = F[pairs[:, 0]], F[pairs[:, 1]]
        shared = np.zeros(len(pairs), bool)
        for a in range(3):
            for b in range(3):
                shared |= fa[:, a] == fb[:, b]
        pairs = pairs[~shared]
    if len(pairs):
        # cheap reject: bounding boxes must overlap
        lo, hi = T.min(axis=1), T.max(axis=1)
        box = np.all((lo[pairs[:, 0]] <= hi[pairs[:, 1]] + tol) & (lo[pairs[:, 1]] <= hi[pairs[:, 0]] + tol), axis=1)
        pairs = pairs[box]
    count = 0
    for s in range(0, len(pairs), _PAIR_CHUNK):
        if time.monotonic() > deadline:
            return "timed out", count
        chunk = pairs[s:s + _PAIR_CHUNK]
        count += int(_pairs_cross(T[chunk[:, 0]], T[chunk[:, 1]], tol).sum())
    return "done", count


# Local thickness by ray casting (task 013): from the centre of a sample of triangles a ray is shot
# inwards along the normal; the distance to the first surface it meets is the part's thickness there.
# A hit whose face is nearly parallel to the start face (a plate, a fin, a wall) is "plate-like"; the
# sharp wedge of a trailing edge is thin too, but its faces meet at an angle and the mesher handles
# it through its feature edges, so it is kept apart. Only thicknesses below a tenth of the part's
# size matter (the mesher's surface cells are about 1/128 of the reference length).
THICKNESS_MAX_RAYS = 4000
THICKNESS_SECONDS = 10.0
THICKNESS_MAX_FRACTION = 0.1  # of the box diagonal
THICKNESS_PARALLEL_COS = 0.978  # cos 12 deg: opposite faces within 12 deg of parallel are a plate / fin
THICKNESS_KEEP = 256  # plate-like samples kept (sorted by thickness, merged by area)
_RAY_CHUNK = 32


def thickness(
    tris: np.ndarray, keep: np.ndarray, outward: bool, *,
    max_rays: int = THICKNESS_MAX_RAYS, seconds: float = THICKNESS_SECONDS,
    max_fraction: float = THICKNESS_MAX_FRACTION,
) -> dict:
    """Area-weighted local thickness of a closed surface (file units).

    Returns ``status`` ("done" | "timed out" | "skipped"), ``rays`` cast, ``hits`` (rays that met
    the surface within ``max_fraction`` of the diagonal), ``min`` (thinnest hit of any kind),
    ``min_plate``, ``plate``: plate-like samples as ``[[thickness, area], ...]`` sorted by
    thickness, each area the surface area the sample stands for (all samples together stand for
    the whole surface), merged down to at most :data:`THICKNESS_KEEP` entries, and ``samples``:
    every hit as parallel lists ``index`` (the triangle's position among the non-degenerate
    triangles, i.e. in the cleaned STL), ``t`` (thickness) and ``plate`` (0/1), from which the
    mesher's refinement band around thin parts is built (:mod:`windlab.geometry.thin`).
    """
    idx = np.flatnonzero(keep)
    out = {"status": "skipped", "rays": 0, "hits": 0, "min": None, "min_plate": None, "plate": [],
           "max_ray": None, "samples": {"index": [], "t": [], "plate": []}}
    if len(idx) < 4:
        return out
    T = tris[idx]
    n = len(T)
    e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    nrm = np.cross(e1, e2)
    area2 = np.linalg.norm(nrm, axis=1)
    unit = nrm / area2[:, None]
    if not outward:
        unit = -unit
    area = 0.5 * area2
    total = float(area.sum())
    lo, hi = T.min(axis=1), T.max(axis=1)
    diag = float(np.linalg.norm(hi.max(axis=0) - lo.min(axis=0)))
    if not (total > 0 and diag > 0):
        return out
    tmax = max_fraction * diag
    eps = 1e-7 * diag
    if n <= max_rays:
        sel = np.arange(n)
        weight = area
    else:  # importance sample by area: each ray then stands for an equal share of the surface
        rng = np.random.default_rng(12345)
        sel = rng.choice(n, size=max_rays, replace=False, p=area / total)
        weight = np.full(max_rays, total / max_rays)
    origins = T[sel].mean(axis=1)
    # rays sorted by the grid cell (of side tmax) their start lies in, so that a chunk of rays sits in
    # one small region and only the triangles around it need testing
    cell = np.floor((origins - lo.min(axis=0)) / tmax).astype(np.int64)
    order = np.lexsort((cell[:, 2], cell[:, 1], cell[:, 0]))
    sel, weight, origins = sel[order], weight[order], origins[order]
    dirs = -unit[sel]  # inwards
    m = len(sel)
    best_t = np.full(m, np.inf)
    best_dot = np.zeros(m)
    deadline = time.monotonic() + seconds
    cast = np.zeros(m, bool)
    starts = np.arange(0, m, _RAY_CHUNK)
    np.random.default_rng(7).shuffle(starts)  # chunks in random order: a timeout still samples the whole part
    for s in starts:
        if time.monotonic() > deadline:
            break
        o, d = origins[s:s + _RAY_CHUNK], dirs[s:s + _RAY_CHUNK]
        cast[s:s + _RAY_CHUNK] = True
        end = o + d * tmax
        seg_lo, seg_hi = np.minimum(o, end) - eps, np.maximum(o, end) + eps
        near = np.flatnonzero(np.all(hi >= seg_lo.min(axis=0), axis=1) & np.all(lo <= seg_hi.max(axis=0), axis=1))
        if not len(near):
            continue
        mask = (np.all(seg_lo[:, None, :] <= hi[near][None, :, :], axis=2)
                & np.all(seg_hi[:, None, :] >= lo[near][None, :, :], axis=2))
        ri, ti = np.nonzero(mask)
        ti = near[ti]
        own = ti != sel[s:s + _RAY_CHUNK][ri]
        ri, ti = ri[own], ti[own]
        if len(ri):
            D = d[ri]
            E1, E2 = e1[ti], e2[ti]
            P = np.cross(D, E2)
            det = np.einsum("ij,ij->i", E1, P)
            ok = np.abs(det) > 1e-9 * area2[ti]
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tv = o[ri] - T[ti, 0]
            u = np.einsum("ij,ij->i", tv, P) * inv
            Q = np.cross(tv, E1)
            v = np.einsum("ij,ij->i", D, Q) * inv
            t = np.einsum("ij,ij->i", E2, Q) * inv
            hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > eps) & (t <= tmax)
            if hit.any():
                rh, th, tt = ri[hit], ti[hit], t[hit]
                order = np.lexsort((tt, rh))
                rh, th, tt = rh[order], th[order], tt[order]
                first_ray, first = np.unique(rh, return_index=True)
                best_t[s + first_ray] = tt[first]
                best_dot[s + first_ray] = np.einsum("ij,ij->i", unit[sel[s + first_ray]], unit[th[first]])
    done = int(cast.sum())
    status = "done" if done >= m else "timed out"
    got = cast & np.isfinite(best_t)
    t_all, dot_all, w_all = best_t[got], best_dot[got], weight[got]
    plate = dot_all <= -THICKNESS_PARALLEL_COS
    # scale the areas so the rays cast stand for the whole surface (matters when the deadline cut in)
    w_cast = float(weight[cast].sum())
    scale = total / w_cast if w_cast > 0 else 1.0
    pairs = _merge_samples(t_all[plate], w_all[plate] * scale, THICKNESS_KEEP)
    out.update({
        "status": status, "rays": done, "hits": int(got.sum()), "max_ray": tmax,
        "min": float(t_all.min()) if len(t_all) else None,
        "min_plate": float(t_all[plate].min()) if plate.any() else None,
        "plate": pairs,
        "samples": {"index": sel[got].tolist(), "t": np.round(t_all, 10).tolist(), "plate": plate.astype(int).tolist()},
    })
    return out


def _merge_samples(t: np.ndarray, w: np.ndarray, keep: int) -> list[list[float]]:
    """(thickness, area) samples sorted by thickness; more than ``keep`` of them are merged into
    ``keep`` groups of neighbours (area-weighted mean thickness, summed area)."""
    if not len(t):
        return []
    order = np.argsort(t, kind="stable")
    t, w = t[order], w[order]
    if len(t) > keep:
        groups = np.array_split(np.arange(len(t)), keep)
        t = np.array([float(np.average(t[g], weights=w[g])) if w[g].sum() > 0 else float(t[g].mean()) for g in groups])
        w = np.array([float(w[g].sum()) for g in groups])
    return [[float(a), float(b)] for a, b in zip(t, w, strict=True)]


_OUT_DTYPE = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def write_clean(path: str, tris: np.ndarray, keep: np.ndarray, scale: float, flip: bool) -> int:
    t = tris[keep] * scale
    if flip:
        t = t[:, ::-1]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, norm, out=np.zeros_like(n), where=norm > 0)
    rec = np.zeros(len(t), dtype=_OUT_DTYPE)
    rec["normal"], rec["v"] = n, t
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"WindLab binary STL (metres)".ljust(80, b" "))  # must not start with "solid"
        fh.write(np.uint32(len(t)).tobytes())
        fh.write(rec.tobytes())
    return int(len(t))


def main(argv: list[str]) -> int:
    path, max_triangles = argv[1], int(argv[2])
    out_path, scale = (argv[3], float(argv[4])) if len(argv) >= 5 else (None, 1.0)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        fmt, tris, normals = read_stl(data, max_triangles)
        del data
        stats, keep, faces, tol = analyse(tris, normals)
        check, crossings = self_intersections(tris, faces, keep, tol)
        stats["self_intersection_check"] = check
        stats["self_intersections"] = crossings
        out = {"format": fmt, **stats}
        closed = (stats["open_edges"] == 0 and stats["nonmanifold_edges"] == 0 and stats["inconsistent_edges"] == 0
                  and crossings == 0)
        if closed:
            out["thickness"] = thickness(tris, keep, outward=stats["signed_volume"] > 0)
        if out_path and closed:
            out["written_triangles"] = write_clean(out_path, tris, keep, scale, flip=stats["signed_volume"] < 0)
    except BadFile as e:
        print(json.dumps({"error": str(e)}))
        return 2
    except MemoryError:
        print(json.dumps({"error": "the file needs more memory to check than allowed"}))
        return 3
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
