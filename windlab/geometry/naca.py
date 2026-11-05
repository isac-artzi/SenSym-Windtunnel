"""NACA 4-digit wing generator.

Builds a closed (watertight) triangulated wing from a NACA 4-digit section:

* x is the chord direction (leading edge of the root at x = 0, air flows towards +x),
* y is the span direction (wing centred on y = 0),
* z is "up" (lift direction at zero angle of attack).

The section uses the closed-trailing-edge thickness formula (last coefficient -0.1036),
so the upper and lower surfaces meet in a single trailing-edge point. Tip caps are flat.
Optional linear taper keeps the quarter-chord line straight (unswept).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from windlab.geometry.stl import write_binary_stl

# Closed trailing edge variant of the NACA thickness polynomial.
_THICKNESS_COEFFS = (0.2969, -0.1260, -0.3516, 0.2843, -0.1036)


@dataclass(frozen=True)
class NacaCode:
    max_camber: float  # m, fraction of chord
    camber_pos: float  # p, fraction of chord
    thickness: float  # t, fraction of chord

    @classmethod
    def parse(cls, code: str) -> NacaCode:
        if not isinstance(code, str) or not re.fullmatch(r"\d{4}", code):
            raise ValueError(f"NACA code must be four digits, e.g. '0012' (got {code!r})")
        m = int(code[0]) / 100.0
        p = int(code[1]) / 10.0
        t = int(code[2:]) / 100.0
        if not 0.01 <= t <= 0.40:
            raise ValueError("NACA thickness (last two digits) must be between 01 and 40")
        if m > 0 and p == 0:
            raise ValueError("A cambered NACA section needs a camber position (second digit 1-9)")
        return cls(m, p, t)


def naca4_section(code: str, n_per_side: int = 100) -> np.ndarray:
    """Return the section outline for a unit chord as an (N, 2) array of (x, z) points.

    The polygon is closed implicitly (last point connects to first), counter-clockwise
    when viewed with x to the right and z up: trailing edge -> upper surface -> leading
    edge -> lower surface. Leading and trailing edge points appear exactly once.
    Points are clustered at both edges with cosine spacing.
    """
    if n_per_side < 8:
        raise ValueError("n_per_side must be at least 8")
    nc = NacaCode.parse(code)
    m, p, t = nc.max_camber, nc.camber_pos, nc.thickness

    beta = np.linspace(0.0, np.pi, n_per_side + 1)
    x = 0.5 * (1.0 - np.cos(beta))  # 0 (LE) ... 1 (TE)
    a0, a1, a2, a3, a4 = _THICKNESS_COEFFS
    yt = 5.0 * t * (a0 * np.sqrt(x) + a1 * x + a2 * x**2 + a3 * x**3 + a4 * x**4)
    yt[0] = 0.0
    yt[-1] = 0.0  # closed TE formula is ~1e-17 here; make it exact

    yc = np.zeros_like(x)
    dyc = np.zeros_like(x)
    if m > 0:
        fwd = x < p
        aft = ~fwd
        yc[fwd] = m / p**2 * (2 * p * x[fwd] - x[fwd] ** 2)
        dyc[fwd] = 2 * m / p**2 * (p - x[fwd])
        yc[aft] = m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x[aft] - x[aft] ** 2)
        dyc[aft] = 2 * m / (1 - p) ** 2 * (p - x[aft])
    theta = np.arctan(dyc)

    xu = x - yt * np.sin(theta)
    zu = yc + yt * np.cos(theta)
    xl = x + yt * np.sin(theta)
    zl = yc - yt * np.cos(theta)

    upper = np.column_stack([xu, zu])[::-1]  # TE ... LE (includes both)
    lower = np.column_stack([xl, zl])[1:-1]  # just after LE ... just before TE
    return np.vstack([upper, lower])


def _polygon_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _ear_clip(poly: np.ndarray) -> np.ndarray:
    """Triangulate a simple counter-clockwise polygon. Returns (M, 3) vertex indices."""
    n = len(poly)
    idx = list(range(n))
    tris: list[tuple[int, int, int]] = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(idx) > 3:
        guard += 1
        if guard > 4 * n * n:
            raise RuntimeError("tip-cap triangulation failed (section outline not simple?)")
        k = len(idx)
        pts = poly[idx]
        best = None
        best_quality = -1.0
        for i in range(k):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % k]
            area2 = cross(a, b, c)
            if area2 <= 0:  # reflex or degenerate corner
                continue
            others = np.delete(pts, [(i - 1) % k, i, (i + 1) % k], axis=0)
            if len(others):
                d1 = (b[0] - a[0]) * (others[:, 1] - a[1]) - (b[1] - a[1]) * (others[:, 0] - a[0])
                d2 = (c[0] - b[0]) * (others[:, 1] - b[1]) - (c[1] - b[1]) * (others[:, 0] - b[0])
                d3 = (a[0] - c[0]) * (others[:, 1] - c[1]) - (a[1] - c[1]) * (others[:, 0] - c[0])
                if np.any((d1 >= 0) & (d2 >= 0) & (d3 >= 0)):
                    continue  # another vertex lies inside this ear
            # prefer well-shaped ears: area relative to longest edge squared
            longest = max(np.sum((b - a) ** 2), np.sum((c - b) ** 2), np.sum((a - c) ** 2))
            quality = area2 / longest
            if quality > best_quality:
                best, best_quality = i, quality
        if best is None:
            raise RuntimeError("tip-cap triangulation failed: no ear found")
        tris.append((idx[best - 1], idx[best], idx[(best + 1) % k]))
        del idx[best]
    tris.append((idx[0], idx[1], idx[2]))
    return np.array(tris, dtype=np.int64)


@dataclass(frozen=True)
class Wing:
    vertices: np.ndarray  # (V, 3) float64, metres
    faces: np.ndarray  # (F, 3) int64, outward-facing (counter-clockwise seen from outside)
    code: str
    root_chord: float
    tip_chord: float
    span: float

    @property
    def taper(self) -> float:
        return self.tip_chord / self.root_chord

    @property
    def reference_area(self) -> float:
        """Planform area S (m^2), the usual reference area for wing coefficients."""
        return 0.5 * self.span * (self.root_chord + self.tip_chord)

    @property
    def mean_aero_chord(self) -> float:
        """Mean aerodynamic chord (m) of the trapezoidal planform."""
        lam = self.taper
        return 2.0 / 3.0 * self.root_chord * (1 + lam + lam**2) / (1 + lam)

    @property
    def quarter_chord_point(self) -> tuple[float, float, float]:
        """Moment reference point: quarter chord of the root on the centre line."""
        return (0.25 * self.root_chord, 0.0, 0.0)

    @property
    def bounds(self) -> np.ndarray:
        return np.array([self.vertices.min(axis=0), self.vertices.max(axis=0)])

    def write_stl(self, path, solid_name: str = "body") -> None:
        write_binary_stl(path, self.vertices, self.faces, solid_name=solid_name)


def naca_wing(
    code: str = "0012",
    chord: float = 0.3,
    span: float = 0.6,
    taper: float = 1.0,
    n_per_side: int = 100,
    n_span: int | None = None,
) -> Wing:
    """Generate a watertight NACA 4-digit wing.

    chord   root chord in metres
    span    full tip-to-tip span in metres (the wing is centred on y = 0)
    taper   tip chord / root chord, 0.2 ... 1.0; the quarter-chord line stays straight
    n_per_side  points on each of upper and lower surface (cosine spaced)
    n_span  number of spanwise panels (default: about one per 5 % of chord, even number)
    """
    if not (0.001 <= chord <= 100.0):
        raise ValueError("chord must be between 1 mm and 100 m")
    if not (0.001 <= span <= 1000.0):
        raise ValueError("span must be between 1 mm and 1000 m")
    if not (0.2 <= taper <= 1.0):
        raise ValueError("taper ratio must be between 0.2 and 1.0")

    section = naca4_section(code, n_per_side)  # (P, 2), CCW in (x, z)
    n_pts = len(section)

    if n_span is None:
        n_span = max(2, int(np.ceil(span / (0.05 * chord))))
    n_span = max(2, int(n_span) + (int(n_span) % 2))  # even, so a station sits at y = 0
    ys = np.linspace(-0.5 * span, 0.5 * span, n_span + 1)

    verts = np.empty(((n_span + 1) * n_pts, 3))
    for j, y in enumerate(ys):
        c = chord * (1.0 - (1.0 - taper) * abs(2.0 * y / span))
        x_le = 0.25 * (chord - c)  # straight quarter-chord line
        sl = slice(j * n_pts, (j + 1) * n_pts)
        verts[sl, 0] = x_le + c * section[:, 0]
        verts[sl, 1] = y
        verts[sl, 2] = c * section[:, 1]

    faces = []
    i = np.arange(n_pts)
    ip = (i + 1) % n_pts
    for j in range(n_span):
        a = j * n_pts + i
        b = j * n_pts + ip
        c = (j + 1) * n_pts + ip
        d = (j + 1) * n_pts + i
        # Section runs CCW in (x, z) and stations advance along +y, so (a, c, b)
        # gives outward normals (tangent x span points into the wing).
        faces.append(np.column_stack([a, c, b]))
        faces.append(np.column_stack([a, d, c]))

    cap = _ear_clip(section)  # CCW in (x, z)
    # Viewed from -y (the left tip), CCW in (x, z) points outward along -y.
    faces.append(cap)
    faces.append(n_span * n_pts + cap[:, ::-1])
    faces = np.vstack(faces).astype(np.int64)

    # Orient outward: positive signed volume.
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    vol = np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0
    if vol < 0:
        faces = faces[:, ::-1]

    return Wing(
        vertices=verts,
        faces=faces,
        code=code,
        root_chord=chord,
        tip_chord=chord * taper,
        span=span,
    )


def section_area(code: str, n_per_side: int = 200) -> float:
    """Area of a unit-chord section (useful for checks)."""
    return _polygon_area(naca4_section(code, n_per_side))
