"""2D NACA 0012 validation case (NASA Turbulence Modeling Resource, Re = 6 million).

The airfoil spans the whole domain, which is one cell thick in y with ``empty`` front
and back planes, so there are no wing tips. The mesh is a structured multi-block
C-grid written by blockMesh; every vertex, curved-edge point, cell count and grading
is computed here and written as a number (same security model as
:mod:`windlab.case_builder`). See :func:`block_mesh` for the block layout.

Coordinates follow the 3D template: chord along +x (leading edge at the origin),
"up" is +z, the span (one cell) is along y. Angle of attack rotates the freestream,
so one mesh serves every alpha.

Wall-resolved: first cell 2e-6 c, i.e. y+ below about 1 at Re 6e6, with
``nutLowReWallFunction`` (nut = 0 at the wall), k = 0 at the wall and the
viscous-sublayer omega value. No wall functions.

Lessons from building it (keep when editing): grid lines must leave the wall nearly
perpendicular (cells have aspect ratios in the thousands; 45 deg skew made k blow up
near the leading edge), curved *side* edges next to tiny chordwise cells fold the
blockMesh interpolation, and far-wake cells must not grow unbounded with grid lines
leaning between differently graded edges (spurious k production at the outlet).
"""

from __future__ import annotations

import json
import math
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from windlab.case_builder import (
    DEFAULT_RUNS_ROOT,
    FREESTREAM_NUT_RATIO,
    FREESTREAM_TU,
    RHO_SEA_LEVEL,
    SPEED_OF_SOUND,
    TEMPLATE_ROOT,
    Case,
    FlowConditions,
    InputError,
    _num,
    _vec,
    freestream_directions,
    render,
    safe_join,
)

TEMPLATE = "airfoil_2d"

# TMR case: Re 6e6 on a 1 m chord. 50 m/s keeps Mach at 0.147 (TMR uses M = 0.15),
# so nu is set to give the target Reynolds number rather than taken from air.
CHORD = 1.0
SPEED = 50.0
REYNOLDS = 6.0e6
NU = SPEED * CHORD / REYNOLDS

# TMR's sharp-trailing-edge NACA 0012 (chord exactly 1, closes at x = 1):
# https://tmbwg.github.io/turbmodels/naca0012_val.html (formerly turbmodels.larc.nasa.gov)
_TMR_SCALE = 0.594689181
_TMR_COEFFS = (0.298222773, -0.127125232, -0.357907906, 0.291984971, -0.105174606)


def tmr_naca0012_half_thickness(x: np.ndarray) -> np.ndarray:
    """Upper-surface z of the TMR NACA 0012 for x in [0, 1] (unit chord)."""
    x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    a0, a1, a2, a3, a4 = _TMR_COEFFS
    z = _TMR_SCALE * (a0 * np.sqrt(x) + a1 * x + a2 * x**2 + a3 * x**3 + a4 * x**4)
    return np.where(x >= 1.0, 0.0, z)


@dataclass(frozen=True)
class AirfoilMesh:
    """Mesh controls, in chords. Defaults give ~190k cells with y+ < 1."""

    farfield: float = 500.0  # C radius and outlet distance (TMR grids use ~500 c)
    inner_ring: float = 2.0  # thickness of the wall-normal inner block ring
    split_x: float = 0.3  # where the front and rear surface blocks meet
    first_cell: float = 2.0e-6  # wall-normal size of the first cell
    wall_growth: float = 1.1  # target wall-normal growth ratio
    # Chordwise spacing. Halving these (to 5e-4 / 8e-3 / 5e-4) changed CL at 10 deg by
    # 0.1 % and CD by 1 %, so this level is close to chordwise grid independence.
    le_spacing: float = 1.0e-3  # at the leading edge
    mid_spacing: float = 1.5e-2  # at x = split_x
    te_spacing: float = 1.0e-3  # at the trailing edge
    wake_growth: float = 1.1  # growth ratio along the wake cut
    # Far-wake cells along the cut must not be too anisotropic: with 10 m long, mm-thin
    # cells the alpha = 0 wake (which runs along the cut) flapped and produced spurious
    # turbulence. At higher alpha the wake leaves the inner ring and this matters less.
    wake_max_cell: float = 2.0  # wake cells stop growing at this length
    wake_split: float = 1.0  # near-wake block length behind the trailing edge
    wake_normal_cell: float = 1.0e-3  # first cell above the wake cut at the split
    outlet_normal_cell: float = 1.5e-2  # first cell above the wake cut at the outlet (~uniform)
    span: float = 0.1  # thickness of the one-cell slab (any value; enters Aref)
    n_curve_points: int = 400  # points per surface used for the polyLine edges


DEFAULT_MESH = AirfoilMesh()  # frozen, so safe as a default argument


# --------------------------------------------------------------------------- spacing


def _series_length(d0: float, q: float, n: int) -> float:
    return d0 * n if abs(q - 1.0) < 1e-12 else d0 * (q**n - 1.0) / (q - 1.0)


def cells_for_end_spacings(length: float, d0: float, d1: float) -> tuple[int, float]:
    """Cell count and blockMesh expansion (last/first) going geometrically from d0 to d1."""
    best = None
    for n in range(2, 5000):
        q = (d1 / d0) ** (1.0 / (n - 1))
        err = abs(_series_length(d0, q, n) - length)
        if best is None or err < best[1]:
            best = (n, err)
        elif err > best[1]:
            break
    return best[0], d1 / d0


def cells_for_growth(length: float, d0: float, q: float) -> int:
    return max(1, math.ceil(math.log(1.0 + length * (q - 1.0) / d0) / math.log(q)))


def expansion_for_first_cell(length: float, d0: float, n: int) -> float:
    """blockMesh expansion (last/first) so that n geometric cells of first size d0 fill length."""
    if n * d0 >= length:
        lo, hi = 1e-6, 1.0
    else:
        lo, hi = 1.0, 2.0
        while _series_length(d0, hi, n) < length:
            hi *= 1.5
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _series_length(d0, mid, n) < length:
            lo = mid
        else:
            hi = mid
    return (0.5 * (lo + hi)) ** (n - 1)


def capped_grading(length: float, d0: float, q: float, d_max: float, n: int | None = None):
    """Geometric growth from d0 by ratio q up to d_max, then uniform cells.

    Returns (n, grading) where grading is a blockMesh multi-grading list
    ``[(length fraction, cell fraction, expansion), ...]``. If ``n`` is given the uniform
    part takes whatever cells remain (its size then only approximates d_max).
    """
    n_g = max(1, math.ceil(math.log(d_max / d0) / math.log(q)) + 1)
    l_g = _series_length(d0, q, n_g)
    if l_g >= length:  # never reaches the cap
        n_all = n or cells_for_growth(length, d0, q)
        return n_all, [(1.0, 1.0, expansion_for_first_cell(length, d0, n_all))]
    n_u = max(1, round((length - l_g) / d_max)) if n is None else n - n_g
    if n_u < 1:
        raise InputError("internal error: too few wake cells for the capped grading")
    n_all = n_g + n_u
    return n_all, [(l_g / length, n_g / n_all, q ** (n_g - 1)), ((length - l_g) / length, n_u / n_all, 1.0)]


def capped_sizes(length: float, d0: float, q: float, d_max: float, n: int | None = None) -> tuple[np.ndarray, int]:
    """Cell sizes of :func:`capped_grading` and the number of cells in its geometric part."""
    n_all, spec = capped_grading(length, d0, q, d_max, n)
    parts = []
    for lf, cf, e in spec:
        k = round(cf * n_all)
        parts.append(np.diff(_geometric_boundaries(lf * length, k, e)))
    return np.concatenate(parts), len(parts[0])


def slice_grading(sizes: np.ndarray, n_geometric: int, a: int, b: int):
    """blockMesh grading for cells a..b-1 of a geometric-then-uniform distribution."""
    seg = sizes[a:b]
    n = b - a
    k = min(max(n_geometric - a, 0), n)  # cells of the geometric part inside the slice
    if k in (0, n):
        return float(seg[-1] / seg[0])
    total = seg.sum()
    return [(seg[:k].sum() / total, k / n, seg[k - 1] / seg[0]), (seg[k:].sum() / total, (n - k) / n, seg[-1] / seg[k])]


# --------------------------------------------------------------------------- mesh


def _surface(x0: float, x1: float, n: int, sign: float) -> np.ndarray:
    """Dense (x, z) points on one surface between x0 and x1, cosine clustered at both ends."""
    t = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, n)))
    x = x0 + (x1 - x0) * t
    return np.column_stack([x, sign * tmr_naca0012_half_thickness(x)])


def _arc_length(pts: np.ndarray) -> float:
    return float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))


def _tmr_slope(x: np.ndarray) -> np.ndarray:
    """dz/dx of the TMR upper surface (infinite at the leading edge)."""
    x = np.asarray(x, dtype=float)
    a0, a1, a2, a3, a4 = _TMR_COEFFS
    with np.errstate(divide="ignore"):
        return _TMR_SCALE * (0.5 * a0 / np.sqrt(x) + a1 + 2 * a2 * x + 3 * a3 * x**2 + 4 * a4 * x**3)


def _upper_normal(x: float) -> tuple[float, float]:
    """Outward unit normal of the upper surface at x (pointing -x at the leading edge)."""
    if x <= 0.0:
        return (-1.0, 0.0)
    s = float(_tmr_slope(x))
    n = math.hypot(s, 1.0)
    return (-s / n, 1.0 / n)


def _geometric_boundaries(length: float, n: int, expansion: float) -> np.ndarray:
    """n + 1 cell boundaries from 0 to length, cell sizes growing geometrically by `expansion`."""
    q = expansion ** (1.0 / (n - 1)) if n > 1 else 1.0
    sizes = q ** np.arange(n)
    return np.concatenate([[0.0], np.cumsum(sizes)]) * (length / sizes.sum())


def block_mesh(m: AirfoilMesh = DEFAULT_MESH, half: bool = False) -> dict:
    """Vertices, blocks, curved edges and patches of the C-grid (unit chord).

    ``half``: only the upper half (z >= 0) with a ``symmetry`` patch on the chord line
    ahead of the leading edge and on the wake cut - for alpha = 0, where the full model's
    wake flaps along the cut and the run diverges in about half the attempts.

    Two rings of blocks around the airfoil and along the wake cut. Along each surface
    the front (0 <= x <= split_x) is cut into several blocks wherever the wall-normal
    direction has turned by ~15 deg; every block side edge is a straight wall-normal
    line, so grid lines leave the wall (nearly) perpendicular, which matters for cells
    with aspect ratios in the thousands. The split points sit on cell boundaries of one
    geometric distribution, so the chordwise spacing is continuous.

    * inner ring, ``m.inner_ring`` chords thick: the boundary-layer cells,
    * outer ring: out to a circle of radius R about the trailing edge, outer vertices
      spread along it (0, +/-45, +/-90 deg) so far-field cells are not slivers,
    * wake: cells grow from the trailing-edge spacing to at most ``wake_max_cell``; the
      wake is split ``wake_split`` chords downstream, where the cut gets a wake-sized
      first cell instead of the wall spacing.
    """
    R, xs, H = m.farfield, m.split_x, m.inner_ring
    xo = 1.0 + R
    y_lo, y_hi = 0.0, m.span

    def circle(phi_deg: float) -> tuple[float, float]:
        """Far field about the trailing edge; phi = 0 is straight ahead of the airfoil."""
        phi = math.radians(phi_deg)
        return (1.0 - R * math.cos(phi), R * math.sin(phi))

    n_front = max(40, int(m.n_curve_points * xs))
    n_rear = max(40, m.n_curve_points - n_front)
    front = _surface(0.0, xs, n_front, +1.0)
    rear = _surface(xs, 1.0, n_rear, +1.0)
    s_front = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(front, axis=0), axis=1))])

    n1, e_front = cells_for_end_spacings(s_front[-1], m.le_spacing, m.mid_spacing)
    n2, e_rear = cells_for_end_spacings(_arc_length(rear), m.mid_spacing, m.te_spacing)
    # Wake: geometric growth from the trailing-edge spacing, capped at wake_max_cell. The
    # far-field wake edge starts from the size of the neighbouring far-field arc cell, so
    # both edges are near-uniform over most of the wake and grid lines there stay upright.
    sizes_w, ng_w = capped_sizes(xo - 1.0, m.te_spacing, m.wake_growth, m.wake_max_cell)
    nw = len(sizes_w)
    rear_far = _geometric_boundaries(R * math.pi / 4, n2, e_rear)
    d_far = min(float(rear_far[-1] - rear_far[-2]), 0.5 * m.wake_max_cell)
    sizes_f, ng_f = capped_sizes(xo - 1.0, d_far, m.wake_growth, m.wake_max_cell, n=nw)
    # The wake is split m.wake_split chords behind the trailing edge. That station gets a
    # wake-sized first cell above the cut, so the wall spacing (y+ ~ 1) does not persist
    # far downstream: blockMesh blends side-edge gradings by position along the block,
    # and without the split the wake-cut cells stayed ~1e-6 c tall for many chords
    # (aspect ratio ~1e5), which made the wake go unstable at alpha = 0.
    iw = int(np.argmin(np.abs(np.cumsum(sizes_w) - m.wake_split))) + 1
    x_ws = 1.0 + float(np.sum(sizes_w[:iw]))
    x_fs = 1.0 + float(np.sum(sizes_f[:iw]))

    # Front split points: cell boundaries where the wall normal has turned by ~15 deg.
    sb = _geometric_boundaries(s_front[-1], n1, e_front)
    xb = np.interp(sb, s_front, front[:, 0])
    theta = np.degrees([math.atan2(*_upper_normal(x)[::-1]) for x in xb])  # 180 at LE ... 90 at xs
    cuts = [0]
    for target in (165.0, 150.0, 135.0, 120.0, 105.0):
        i = int(np.argmin(np.abs(theta - target)))
        if i - cuts[-1] >= 2 and n1 - i >= 2:
            cuts.append(i)
    cuts.append(n1)

    # Stations along one (upper) side: (x, z, normal, far-field angle).
    stations = []
    for i in cuts:
        x = float(xb[i])
        z = float(tmr_naca0012_half_thickness(x)) if 0 < i < n1 else (0.0 if i == 0 else float(front[-1, 1]))
        nrm = _upper_normal(x) if i < n1 else (0.0, 1.0)
        th = 180.0 if i == 0 else math.degrees(math.atan2(nrm[1], nrm[0]))
        stations.append({"x": x, "z": z, "n": nrm, "phi": 45.0 * (180.0 - th) / 90.0, "s": float(sb[i])})
    stations.append({"x": 1.0, "z": 0.0, "n": (0.0, 1.0), "phi": 90.0, "s": None})  # trailing edge
    j_te = len(stations) - 1
    j_ws, j_end = j_te + 1, j_te + 2  # wake split, outlet

    # Segments (between consecutive stations): cells, expansion, kind.
    segments = []
    for k in range(len(cuts) - 1):
        a, b = cuts[k], cuts[k + 1]
        sizes = np.diff(sb[a : b + 1])
        segments.append({"n": b - a, "g": float(sizes[-1] / sizes[0]), "kind": "front"})
    segments.append({"n": n2, "g": e_rear, "kind": "rear"})
    for a, b in ((0, iw), (iw, nw)):
        segments.append({"n": b - a, "g": slice_grading(sizes_w, ng_w, a, b),
                         "g_far": slice_grading(sizes_f, ng_f, a, b), "kind": "wake"})

    # ---- vertices
    coords: dict[tuple, tuple[float, float]] = {}
    sides = (+1,) if half else (+1, -1)

    def key(layer: str, side: int, j: int) -> tuple:
        shared = (layer == "S" and j in (0, j_te, j_ws, j_end)) or j == 0
        return (layer, 0 if shared else side, j)

    for side in sides:
        for j, st in enumerate(stations):
            nx, nz = st["n"][0], side * st["n"][1]
            sx, sz = st["x"], side * st["z"]
            coords[key("S", side, j)] = (sx, sz)
            coords[key("R", side, j)] = (sx + H * nx, sz + H * nz) if j < j_te else (1.0, side * H)
            px, pz = circle(st["phi"])
            coords[key("P", side, j)] = (px, side * pz)
        coords[key("S", side, j_ws)] = (x_ws, 0.0)
        coords[key("R", side, j_ws)] = (x_ws, side * H)
        coords[key("P", side, j_ws)] = (x_fs, side * R)
        coords[key("S", side, j_end)] = (xo, 0.0)
        coords[key("R", side, j_end)] = (xo, side * H)
        coords[key("P", side, j_end)] = (xo, side * R)
    names = list(coords)
    idx = {k: i for i, k in enumerate(names)}
    nv = len(names)

    def v(k: tuple, hi: bool) -> int:
        return idx[k] + (nv if hi else 0)

    # ---- wall-normal cell counts and side-edge gradings
    zs = stations[-2]["z"]
    n_in = cells_for_growth(H - zs, m.first_cell, m.wall_growth)
    n_out = cells_for_growth(R - H, m.first_cell * m.wall_growth**n_in, m.wall_growth)
    g_in, g_out = {}, {}
    for side in sides:
        for j in range(j_end + 1):
            s_, r_ = (coords[key(layer, side, j)] for layer in "SR")
            first = {j_end: m.outlet_normal_cell, j_ws: m.wake_normal_cell}.get(j, m.first_cell)
            g_in[side, j] = expansion_for_first_cell(math.dist(s_, r_), first, n_in)
    # Every outer-ring side edge starts with the same cell (the one that continues the
    # trailing-edge wall spacing), so rows of outer-ring cells stay parallel. Edges that
    # differ (wake split, outlet) only get a cell-size jump at the ring interface, 2 c
    # from the cut; per-edge matching tilted the rows and the run diverged there.
    d_ring = m.first_cell * g_in[1, j_te] * m.wall_growth
    for side in sides:
        for j in range(j_end + 1):
            d_out = math.dist(coords[key("R", side, j)], coords[key("P", side, j)])
            g_out[side, j] = expansion_for_first_cell(d_out, min(d_ring, d_out / n_out), n_out)

    # ---- blocks, edges, patches
    blocks, empty, edges = [], [], []
    airfoil, farfield, symmetry = [], [], []

    def quad(a: tuple, b: tuple) -> list[int]:
        return [v(a, False), v(b, False), v(b, True), v(a, True)]

    def add_block(a, b, c, d, nx, ny, gi, go, g0, g1):
        ax, az = np.subtract(coords[b], coords[a])
        bx, bz = np.subtract(coords[d], coords[a])
        # x1 x x2 points along -y when (x1, x2) turn counter-clockwise in (x, z);
        # blockMesh needs x3 along x1 x x2, so then the first face lies on the far plane.
        first_hi = (ax * bz - az * bx) > 0
        bottom = [v(k, first_hi) for k in (a, b, c, d)]
        top = [v(k, not first_hi) for k in (a, b, c, d)]
        grading = [gi, go, go, gi, g0, g1, g1, g0, 1.0, 1.0, 1.0, 1.0]
        blocks.append({"vertices": bottom + top, "cells": [nx, ny, 1], "grading": grading})
        empty.extend([bottom, top])

    def add_edge(kind, a, b, pts):
        for hi in (False, True):
            edges.append({"type": kind, "from": v(a, hi), "to": v(b, hi), "y": y_hi if hi else y_lo,
                          "points": [list(p) for p in pts]})

    for side in sides:
        for j, seg in enumerate(segments):
            S0, S1 = key("S", side, j), key("S", side, j + 1)
            R0, R1 = key("R", side, j), key("R", side, j + 1)
            P0, P1 = key("P", side, j), key("P", side, j + 1)
            g, nx = seg["g"], seg["n"]
            g_far = seg.get("g_far", g)
            add_block(S0, S1, R1, R0, nx, n_in, g, g, g_in[side, j], g_in[side, j + 1])
            add_block(R0, R1, P1, P0, nx, n_out, g, g_far, g_out[side, j], g_out[side, j + 1])
            if seg["kind"] == "wake":
                if half:
                    symmetry.append(quad(S0, S1))  # the wake cut, z = 0
                continue
            if seg["kind"] == "front":
                s0, s1 = stations[j]["s"], stations[j + 1]["s"]
                inner = (s_front > s0) & (s_front < s1)
                pts = front[inner]
            else:
                pts = rear[1:-1]
            add_edge("polyLine", S0, S1, [(x, side * z) for x, z in pts])
            mid = circle(0.5 * (stations[j]["phi"] + stations[j + 1]["phi"]))
            add_edge("arc", P0, P1, [(mid[0], side * mid[1])])
            airfoil.append(quad(S0, S1))
        for j in range(len(segments)):
            farfield.append(quad(key("P", side, j), key("P", side, j + 1)))
        farfield.append(quad(key("S", side, j_end), key("R", side, j_end)))
        farfield.append(quad(key("R", side, j_end), key("P", side, j_end)))
        if half:  # the chord line ahead of the leading edge (station 0 is the stagnation line)
            symmetry.append(quad(key("S", side, 0), key("R", side, 0)))
            symmetry.append(quad(key("R", side, 0), key("P", side, 0)))

    vertices = [[x, y_lo, z] for x, z in coords.values()] + [[x, y_hi, z] for x, z in coords.values()]
    return {
        "vertices": vertices,
        "blocks": blocks,
        "edges": edges,
        "patches": {"airfoil": airfoil, "farfield": farfield, "frontAndBack": empty, "symmetry": symmetry},
        "half": half,
        "cells": int(sum(b["cells"][0] * b["cells"][1] for b in blocks)),
        "counts": {"front": n1, "front_blocks": len(cuts) - 1, "rear": n2, "wake": nw, "near_wake": iw,
                   "normal_inner": n_in, "normal_outer": n_out},
    }


def _grading(values: list) -> str:
    """edgeGrading entries: a number, or a multi-grading list of (fraction, cells, expansion)."""
    out = []
    for g in values:
        if isinstance(g, (list, tuple)):
            out.append("(" + " ".join(f"({_vec(part)})" for part in g) + ")")
        else:
            out.append(_num(g))
    return " ".join(out)


def _block_mesh_values(bm: dict) -> dict[str, str]:
    def ids(lst):
        return " ".join(_num(i) for i in lst)

    verts = "\n".join(f"    ({_vec(p)})" for p in bm["vertices"])
    blocks = "\n".join(
        f"    hex ({ids(b['vertices'])}) ({ids(b['cells'])}) edgeGrading ({_grading(b['grading'])})"
        for b in bm["blocks"]
    )
    edges = []
    for e in bm["edges"]:
        pts = " ".join(f"({_num(x)} {_num(e['y'])} {_num(z)})" for x, z in e["points"])
        if e["type"] == "arc":
            edges.append(f"    arc {_num(e['from'])} {_num(e['to'])} {pts}")
        else:
            edges.append(f"    polyLine {_num(e['from'])} {_num(e['to'])}\n    (\n        {pts}\n    )")

    def faces(name):
        return "\n".join(f"            ({ids(f)})" for f in bm["patches"][name])

    symmetry = ""
    if bm["patches"].get("symmetry"):
        # Named chordPlane, not "symmetry": caseDicts/setConstraintTypes has an entry of
        # that name with type symmetry; the symmetryPlane group entry gives the fields' BC.
        symmetry = ("    chordPlane\n    {\n        type symmetryPlane;\n        faces\n        (\n"
                    + faces("symmetry") + "\n        );\n    }")
    return {
        "VERTICES": verts,
        "BLOCKS": blocks,
        "EDGES": "\n".join(edges),
        "AIRFOIL_FACES": faces("airfoil"),
        "FARFIELD_FACES": faces("farfield"),
        "EMPTY_FACES": faces("frontAndBack"),
        "SYMMETRY_PATCH": symmetry,
    }


# --------------------------------------------------------------------------- case

# Validation runs need tighter settling than the everyday SIM-10 rule.
VALIDATION_CONVERGENCE = {"window": 200, "cl_tol": 0.001, "cd_tol": 0.002, "cl_floor": 5e-4,
                          "residual_tol": 1e-5, "min_iterations": 500}
# At alpha = 0 the full model's CL wanders around zero (a slowly decaying oscillation of
# the wake along the C-grid cut) and the cross-flow residuals plateau near 1e-2, so the
# rule above never fires; worse, the oscillation grew and the run diverged in two of
# four attempts (2026-11-03). Alpha = 0 is therefore run as a HALF MODEL with a symmetry
# plane (block_mesh(half=True)): the antisymmetric mode cannot exist, CL is zero by
# construction, and the stop rule judges CD only - mean of the last 200 iterations within
# 0.2 % of the mean 500 iterations earlier (CD creeps up slowly while the wake settles,
# so a short lag stops too early), after at least 2500 iterations (the earlier full-model
# run stopped at 1535 with CD 2 % below its long-run value).
ZERO_ALPHA_CONVERGENCE = {"window": 200, "lag": 500, "cl_tol": 1.0, "cd_tol": 0.002, "cl_floor": 1.0,
                          "residual_tol": None, "min_iterations": 2500}
MAX_ITERATIONS = 6000
STARTUP_ITERATIONS = 300


def case_parameters(alpha_deg: float, n_procs: int, mesh: AirfoilMesh = DEFAULT_MESH,
                    reynolds: float = REYNOLDS, half: bool | None = None) -> dict:
    """``reynolds`` other than the TMR's 6e6 keeps the speed (Mach) and changes nu, e.g. to
    run the wall-resolved 2D section at the Reynolds number of a 3D wing run.
    ``half`` (default: alpha == 0) builds the upper half with a symmetry plane; then
    ``a_ref`` is halved so CD is the full section's, and CL/Cm are meaningless (zero by
    symmetry)."""
    cond = FlowConditions(airspeed=SPEED, alpha_deg=alpha_deg)  # validates alpha
    if not 1e4 <= float(reynolds) <= 1e8:
        raise InputError("2D Reynolds number must be between 1e4 and 1e8")
    half = cond.alpha_deg == 0 if half is None else bool(half)
    if half and cond.alpha_deg != 0:
        raise InputError("the half model (symmetry plane) is only valid at alpha = 0")
    nu = SPEED * CHORD / float(reynolds)
    n_procs = int(max(1, min(int(n_procs), 64)))
    U = cond.airspeed
    drag, lift = freestream_directions(cond.alpha_deg)
    k_inf = 1.5 * (FREESTREAM_TU * U) ** 2
    nut_inf = FREESTREAM_NUT_RATIO * nu
    return {
        "template": TEMPLATE,
        "n_procs": n_procs,
        "airspeed": U,
        "alpha_deg": cond.alpha_deg,
        "chord": CHORD,
        "reynolds": U * CHORD / nu,
        "mach": U / SPEED_OF_SOUND,
        "rho": RHO_SEA_LEVEL,
        "nu": nu,
        "a_ref": CHORD * mesh.span * (0.5 if half else 1.0),
        "half_model": half,
        "l_ref": CHORD,
        "cofr": [0.25 * CHORD, 0.0, 0.0],
        "drag_dir": drag.tolist(),
        "lift_dir": lift.tolist(),
        "u_vec": (U * drag).tolist(),
        "k_inf": k_inf,
        "omega_inf": k_inf / nut_inf,
        "nut_inf": nut_inf,
        "max_iterations": MAX_ITERATIONS,
        "convergence": dict(ZERO_ALPHA_CONVERGENCE if half else VALIDATION_CONVERGENCE),
        # first-order momentum and lower relaxation until the potential-flow start has
        # washed out, then the .final dictionaries (see windlab.runner.StartupSwitch)
        "startup": {"iterations": STARTUP_ITERATIONS, "files": ["system/fvSchemes", "system/fvSolution"]},
        "mesh_controls": asdict(mesh),
    }


def build_airfoil_2d_case(
    alpha_deg: float,
    n_procs: int = 4,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    mesh: AirfoilMesh = DEFAULT_MESH,
    reynolds: float = REYNOLDS,
    half: bool | None = None,
) -> Case:
    """Write a ready-to-run 2D NACA 0012 case to ``<runs_root>/<uuid4>/``."""
    p = case_parameters(alpha_deg, n_procs, mesh, reynolds, half)
    bm = block_mesh(mesh, half=p["half_model"])
    if bm["cells"] > 500_000:
        raise InputError("2D mesh would exceed 500k cells")
    p["cells_planned"] = bm["cells"]
    p["cell_counts"] = bm["counts"]
    values = {
        **_block_mesh_values(bm),
        "N_PROCS": _num(p["n_procs"]),
        "MAX_ITERATIONS": _num(p["max_iterations"]),
        "RHO": _num(p["rho"]),
        "NU": _num(p["nu"]),
        "U_MAG": _num(p["airspeed"]),
        "U_VEC": _vec(p["u_vec"]),
        "DRAG_DIR": _vec(p["drag_dir"]),
        "LIFT_DIR": _vec(p["lift_dir"]),
        "COFR": _vec(p["cofr"]),
        "L_REF": _num(p["l_ref"]),
        "A_REF": _num(p["a_ref"]),
        "K_INF": _num(p["k_inf"]),
        "OMEGA_INF": _num(p["omega_inf"]),
        "NUT_INF": _num(p["nut_inf"]),
        "TU_PERCENT": _num(FREESTREAM_TU * 100),
        "NUT_RATIO": _num(FREESTREAM_NUT_RATIO),
    }

    src = TEMPLATE_ROOT / TEMPLATE
    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    dest = safe_join(runs_root, run_id)
    dest.mkdir(mode=0o750)
    try:
        for f in sorted(src.rglob("*")):
            if f.is_symlink():
                raise InputError("template contains a symlink")
            out = dest / f.relative_to(src)
            if f.is_dir():
                out.mkdir(exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render(f.read_text(), values))
        (dest / "windlab_case.json").write_text(json.dumps({"run_id": run_id, **p}, indent=2))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return Case(run_id=run_id, path=dest, params=p)


__all__ = [
    "AirfoilMesh",
    "NU",
    "REYNOLDS",
    "SPEED",
    "block_mesh",
    "build_airfoil_2d_case",
    "case_parameters",
    "tmr_naca0012_half_thickness",
]
