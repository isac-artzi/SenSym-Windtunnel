"""Thin features: what the mesh does about plates, fins and walls thinner than its cells (task 013).

The STL worker measures the local thickness of every closed upload by ray casting
(:func:`windlab.geometry._stl_worker.thickness`): plate-like samples ``[thickness, area]`` where
two nearly parallel faces are close together, plus the per-triangle samples behind them. This
module turns those, for a given surface cell size, into a decision, a refinement band and plain
words:

* a plate-like feature thinner than :data:`THIN_CELLS` surface cells, covering at least
  :data:`MIN_AREA_SHARE` of the surface, is thin. The triangles that make it up (the sampled thin
  ones, grown to their unsampled neighbours) are written as ``thin.stl`` and snappyHexMesh
  refines every cell within :data:`BAND_CELLS` fine cells of them (a ``distance`` refinement
  region) by as many levels above the surface level as it takes for the cells to be no bigger
  than the feature: one level, or :data:`MAX_EXTRA_LEVELS` at most. The rest of the mesh is
  unchanged;
* the band has a cell budget (task 014): :func:`band_cells` estimates how many cells it would
  add; above :data:`CELL_BUDGET` the band gets one level less, and when even one level is over
  the budget the mesh is left plain. The decision carries the estimate, so the page's "Thin
  parts" note and the report can say what was done and why;
* a feature still thinner than :data:`ROUGH_CELLS` of those finest cells is reported as rough,
  with the advice to use the finer preset.

Why: the 4-fin rocket of validation/robustness.md (2 mm fins, 3.3 mm Quick surface cells) was
meshed asymmetrically fin by fin and showed a lift as large as its drag at 0 degrees.
snappyHexMesh's own gap refinement (``gapLevel``) was tried first: it only marks cells that see
both faces of a fin, which stops once the cells are about as small as the fin (86.6k -> 92.8k
cells, lift unchanged), so the band is built from the measured thickness instead. One extra
level (1.6 mm cells, 1.2 across the fin) took the rocket's lift from 67 % of its drag to 0.6 %
for 1.6x the run time; two levels gave zero lift for 3.6x the time. The budget came from the
2 mm flat plate of the same corpus: 97 % of its surface is thin, and the band tripled its cells
(193k -> 640k) and run time for no change in the result.
"""

from __future__ import annotations

import math

import numpy as np

THIN_CELLS = 1.5  # a plate-like feature thinner than this many surface cells is thin: refine around it
MAX_EXTRA_LEVELS = 2  # refinement levels above the surface level in the band, at most (Quick: 1/512 of the length)
BAND_CELLS = 2.0  # half-width of the band around the thin triangles, in fine cells
ROUGH_CELLS = 1.0  # thinner than this many of the finest cells is still rough after refinement
MIN_AREA_SHARE = 0.01  # the thin plate-like area must be at least this share of the surface to count
CELL_BUDGET = 300_000  # the band may add about this many cells; above it, one level less (or no band at all)
N_LAYERS = 5  # boundary-layer cells per surface face (case_builder.N_LAYERS); the band's biggest cost
BUFFER_CELLS = 3  # snappyHexMesh's nCellsBetweenLevels: the ring of coarser cells the band pushes outwards


def band_cells(area: float, surface_cell: float, extra_levels: int, n_layers: int = N_LAYERS) -> int:
    """About how many cells a refinement band ``extra_levels`` above the surface level adds around
    plate-like features whose faces have the (both-sided) area ``area`` m².

    Three parts, each proportional to the area: the band itself, :data:`BAND_CELLS` fine cells
    deep on the fluid side of every face, less the surface-level cells it replaces; the extra
    boundary-layer cells (``n_layers`` per surface face, and a face at the finer level is 4x
    smaller, so there are 4x as many); and one ring of :data:`BUFFER_CELLS` cells at the surface
    level (plus the coarser rings behind it, a geometric series) that the 2:1 rule pushes
    outwards. Calibrated on validation/robustness.md (task 014): within 5 % on the 2 mm plate
    (426k against 447k added) and 15 % on the rocket's fins (44k against 52k); low by 4x on the
    2 m beam's flange, whose upper face was not sampled as thin but lies within the band all the
    same. So the estimate is a coarse guard, not a prediction; the preset's hard cap on cells
    (maxGlobalCells) still stands behind it."""
    if extra_levels <= 0 or not area or not surface_cell or area <= 0 or surface_cell <= 0:
        return 0
    s = float(surface_cell)
    c = s / 2 ** int(extra_levels)
    band = BAND_CELLS / c**2 * (1.0 - (c / s) ** 3)
    layers = n_layers * (1.0 / c**2 - 1.0 / s**2)
    buffer = BUFFER_CELLS * (4.0 / 3.0) / s**2
    return int(round(float(area) * (band + layers + buffer)))


def _below(pairs: list, limit: float) -> tuple[float, float, float] | None:
    """(area-weighted median thickness, area, thinnest) of the plate-like samples thinner than ``limit``."""
    thin = [(float(t), float(a)) for t, a in pairs if float(t) < limit and float(a) > 0]
    if not thin:
        return None
    area = sum(a for _, a in thin)
    cum, median = 0.0, thin[-1][0]
    for t, a in thin:
        cum += a
        if cum >= 0.5 * area:
            median = t
            break
    return median, area, thin[0][0]


def thin_features(thickness: dict | None, surface_cell: float, surface_area: float | None,
                  n_layers: int = N_LAYERS, budget: int = CELL_BUDGET) -> dict | None:
    """What is thin on this part for a mesh with the given surface cell (metres), or None.

    ``thickness`` is the report's thickness block (metres); ``surface_area`` the part's surface
    area in m². The answer carries the typical and the thinnest thickness of the thin plate-like
    features, the surface share they cover, how many cells span them before and after the
    refinement, the levels added (``extra_levels``, 0 when the budget leaves the mesh plain) and
    the levels the thickness alone would call for (``levels_wanted``), the cell estimate for both
    (``added_cells``, ``full_added_cells``) against the ``cell_budget``, ``capped`` when the budget
    took a level away, the thickness limit used, and ``rough``: whether the features are still
    under-resolved on the mesh that will actually be built."""
    pairs = (thickness or {}).get("plate") or []
    if not pairs or not surface_cell or not surface_area or surface_area <= 0:
        return None
    limit = THIN_CELLS * surface_cell
    thin = _below(pairs, limit)
    if thin is None or thin[1] < MIN_AREA_SHARE * surface_area:
        return None
    median, area, thinnest = thin
    # as many levels as it takes for the fine cell to be no bigger than the typical thin feature ...
    wanted = int(min(MAX_EXTRA_LEVELS, max(1, math.ceil(math.log2(surface_cell / median)))))
    # ... unless the band would cost more than the budget: then one level less, or none
    extra = wanted
    while extra > 0 and band_cells(area, surface_cell, extra, n_layers) > budget:
        extra -= 1
    fine_cell = surface_cell / 2 ** extra
    rough = _below(pairs, ROUGH_CELLS * fine_cell)
    is_rough = rough is not None and rough[1] >= MIN_AREA_SHARE * surface_area
    return {
        "thickness": median,
        "thinnest": thinnest,
        "limit": limit,
        "area": area,
        "area_share": area / surface_area,
        "surface_cell": surface_cell,
        "cells_across": median / surface_cell,
        "extra_levels": extra,
        "levels_wanted": wanted,
        "capped": extra < wanted,
        "added_cells": band_cells(area, surface_cell, extra, n_layers),
        "full_added_cells": band_cells(area, surface_cell, wanted, n_layers),
        "cell_budget": int(budget),
        "refined_cell": fine_cell,
        "cells_across_refined": median / fine_cell,
        "band": BAND_CELLS * fine_cell if extra else None,
        "rough": is_rough,
        "rough_thickness": rough[0] if is_rough else None,
    }


def thin_triangles(tris: np.ndarray, thickness: dict | None, limit: float) -> np.ndarray:
    """Mask of the triangles (N, 3, 3), in the order of the cleaned STL) that belong to plate-like
    features thinner than ``limit``: the sampled ones, grown to the unsampled triangles around them
    when the worker could not cast a ray from every triangle (a grid of cells the size of the
    sample spacing; a triangle in a seed's cell or one of its 26 neighbours counts)."""
    n = len(tris)
    mask = np.zeros(n, bool)
    s = (thickness or {}).get("samples") or {}
    index, t, plate = (np.asarray(s.get(k, []), dtype=float) for k in ("index", "t", "plate"))
    if not n or not len(index) or not (len(index) == len(t) == len(plate)):
        return mask
    index = index.astype(np.int64)
    ok = (index >= 0) & (index < n)
    seeds = index[ok & (plate > 0) & (t < limit)]
    if not len(seeds):
        return mask
    mask[seeds] = True
    rays = int((thickness or {}).get("rays", 0))
    if rays >= n:  # every triangle was measured: the mask is exact
        return mask
    centroids = tris.mean(axis=1)
    area = 0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
    seed_area = float(area[seeds].sum()) * n / max(rays, 1)  # the area the seeds stand for
    spacing = np.sqrt(seed_area / len(seeds)) if seed_area > 0 else limit
    h = float(max(spacing, limit))
    lo = centroids.min(axis=0)
    cell = np.floor((centroids - lo) / h).astype(np.int64) + 1
    near = set()
    for c in np.unique(cell[seeds], axis=0):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    near.add(_key(c[0] + dx, c[1] + dy, c[2] + dz))
    keys = _key(cell[:, 0], cell[:, 1], cell[:, 2])
    mask |= np.isin(keys, np.fromiter(near, dtype=np.int64, count=len(near)))
    return mask


def _key(x, y, z):
    return (np.asarray(x, np.int64) << 42) | (np.asarray(y, np.int64) << 21) | np.asarray(z, np.int64)


def _mm(m: float) -> str:
    if m >= 0.01:
        return f"{m * 100:.3g} cm"
    return f"{m * 1000:.2g} mm"


def _cells(n) -> str:
    """A cell count to two significant figures with digit grouping: 426,113 -> '430,000'."""
    n = int(round(float(n or 0)))
    return f"{int(float(f'{n:.2g}')):,}" if n >= 10 else str(n)


def notes(thin: dict | None, preset_label: str = "Quick") -> list[str]:
    """Plain-language lines for the page: what was found and what the mesh does about it. The first
    line is the 'Thin parts' note shown before a run; the lines after it are caveats that the run's
    results repeat (``windlab.app.runs.thin_notes``)."""
    if not thin:
        return []
    share = thin["area_share"] * 100
    share_txt = f"{share:.0f} %" if share >= 1.5 else "about 1 %"
    found = (f"Thin parts: {share_txt} of the surface is a plate or fin about {_mm(thin['thickness'])} thick, less "
             f"than {THIN_CELLS:g} of the {_mm(thin['surface_cell'])} surface cells of the {preset_label} mesh. ")
    extra, capped = int(thin.get("extra_levels", 1)), bool(thin.get("capped"))
    budget, full = _cells(thin.get("cell_budget", CELL_BUDGET)), _cells(thin.get("full_added_cells"))
    if extra == 0:
        out = [found + "WindLab normally refines the mesh around such parts on its own, but here that would add about "
               f"{full} cells (more than the {budget} it allows for this), so the mesh is left as it is.",
               "The mesh was not refined around the thin parts (see the Thin parts note), so results near them may "
               "be rough. If they matter for your test, try \"More accurate, slower\": its cells are smaller "
               "everywhere."]
    elif capped:
        out = [found + f"WindLab refines the mesh around such parts on its own, here to {_mm(thin['refined_cell'])} "
               "cells: one level less than their thickness calls for, because the full refinement would add about "
               f"{full} cells, more than the {budget} WindLab allows for this. The band adds cells and time but keeps "
               "thin fins and plates from being meshed lopsidedly."]
    else:
        out = [found + f"WindLab refines the mesh around such parts on its own (to {_mm(thin['refined_cell'])} cells "
               "there), which adds cells and time but keeps thin fins and plates from being meshed lopsidedly."]
    if thin["rough"]:
        out.append(f"Parts about {_mm(thin['rough_thickness'])} thick are close to the smallest cells WindLab uses "
                   f"here ({_mm(thin['refined_cell'])} on the {preset_label} mesh); results near them are rough. Try "
                   "\"More accurate, slower\", or make them thicker if they are not important for the test.")
    return out


__all__ = ["BAND_CELLS", "BUFFER_CELLS", "CELL_BUDGET", "MAX_EXTRA_LEVELS", "MIN_AREA_SHARE", "N_LAYERS",
           "ROUGH_CELLS", "THIN_CELLS", "band_cells", "notes", "thin_features", "thin_triangles"]
