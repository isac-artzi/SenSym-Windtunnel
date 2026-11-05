"""Built-in bluff and streamlined bodies for the "Streamlining a bluff body" lesson.

Every shape is a closed (watertight) triangulated surface in metres, sized by one or two
plain numbers, with its nose at x = 0 and centred on y = z = 0 (the wind blows along +x,
z is up). Each comes with the usual reference values for a body: the **frontal area**
(the silhouette seen from the front) and the **diameter or height** as the reference
length, so its drag coefficient compares with textbook values (sphere about 0.47,
long cylinder 1.0-1.2, square plate about 1.2, cube about 1.05, streamlined body 0.05-0.1).

Shapes: sphere, circular cylinder across the wind (finite, with flat end caps), flat plate
normal to the flow, cube, and a streamlined teardrop (a body of revolution with the NACA
00xx thickness distribution as its profile, length = fineness ratio x diameter).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from windlab.geometry.naca import _THICKNESS_COEFFS
from windlab.geometry.stl import write_binary_stl

SIZE_RANGE = (0.02, 2.0)  # m, any diameter / side / height / width
FINENESS_RANGE = (2.0, 8.0)  # teardrop length / diameter
CYLINDER_LENGTH_RANGE = (0.5, 20.0)  # in diameters
PLATE_THICKNESS = 0.05  # of the height: thin enough to be "flat", thick enough to mesh


@dataclass(frozen=True)
class Body:
    vertices: np.ndarray  # (V, 3) metres
    faces: np.ndarray  # (F, 3) outward
    shape: str
    name: str
    geometry_class: str  # expected-error class: "bluff" (sharp edges), "bluff-smooth" or "streamlined-body"
    reference_area: float  # frontal area, m^2
    reference_length: float  # diameter or height, m
    params: dict = field(default_factory=dict)
    description: str = ""

    @property
    def bounds(self) -> np.ndarray:
        return np.array([self.vertices.min(axis=0), self.vertices.max(axis=0)])

    @property
    def moment_point(self) -> tuple[float, float, float]:
        lo, hi = self.bounds
        return (0.5 * float(lo[0] + hi[0]), 0.0, 0.0)

    @property
    def reference_how(self) -> str:
        return "frontal area and " + ("height" if self.shape in ("plate", "cube") else "diameter")

    def write_stl(self, path, solid_name: str = "body") -> None:
        write_binary_stl(path, self.vertices, self.faces, solid_name=solid_name)


# --------------------------------------------------------------------------- building blocks


def _outward(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    vol = np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0
    return faces[:, ::-1] if vol < 0 else faces


def revolve(profile_x: np.ndarray, profile_r: np.ndarray, n_circ: int = 120) -> tuple[np.ndarray, np.ndarray]:
    """Revolve a profile r(x) about the x axis into a closed surface.

    The first and last profile points must have r = 0 (they become the poles); all other
    points need r > 0. Returns (vertices, faces) with outward normals.
    """
    x, r = np.asarray(profile_x, float), np.asarray(profile_r, float)
    if len(x) < 3 or r[0] != 0 or r[-1] != 0 or np.any(r[1:-1] <= 0):
        raise ValueError("profile must start and end on the axis and stay off it in between")
    theta = np.linspace(0, 2 * np.pi, n_circ, endpoint=False)
    rings = []
    verts = [np.array([[x[0], 0.0, 0.0]])]
    for xi, ri in zip(x[1:-1], r[1:-1], strict=True):
        start = sum(len(v) for v in verts)
        verts.append(np.column_stack([np.full(n_circ, xi), ri * np.cos(theta), ri * np.sin(theta)]))
        rings.append(start)
    verts.append(np.array([[x[-1], 0.0, 0.0]]))
    V = np.vstack(verts)
    last = len(V) - 1
    k = np.arange(n_circ)
    kn = (k + 1) % n_circ
    faces = [np.column_stack([np.zeros(n_circ, int), rings[0] + kn, rings[0] + k])]
    for a, b in zip(rings, rings[1:], strict=False):
        faces.append(np.column_stack([a + k, a + kn, b + kn]))
        faces.append(np.column_stack([a + k, b + kn, b + k]))
    faces.append(np.column_stack([rings[-1] + k, rings[-1] + kn, np.full(n_circ, last)]))
    F = np.vstack(faces).astype(np.int64)
    return V, _outward(V, F)


def box(size_x: float, size_y: float, size_z: float) -> tuple[np.ndarray, np.ndarray]:
    """An axis-aligned box with its front face at x = 0, centred on y = z = 0 (12 triangles)."""
    V = np.array([[x, y, z] for x in (0.0, size_x) for y in (-size_y / 2, size_y / 2)
                  for z in (-size_z / 2, size_z / 2)])
    # corners: index = 4*xi + 2*yi + zi
    F = np.array([
        [0, 1, 3], [0, 3, 2],  # x = 0 face
        [4, 6, 7], [4, 7, 5],  # x = size_x face
        [0, 4, 5], [0, 5, 1],  # y = -size_y/2
        [2, 3, 7], [2, 7, 6],  # y = +size_y/2
        [0, 2, 6], [0, 6, 4],  # z = -size_z/2
        [1, 5, 7], [1, 7, 3],  # z = +size_z/2
    ], dtype=np.int64)
    return V, _outward(V, F)


def _check(label: str, value: float, rng: tuple[float, float], unit: str = "m") -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    lo, hi = rng
    if not lo <= v <= hi:  # also rejects NaN
        u = f" {unit}" if unit else ""
        raise ValueError(f"{label} must be between {lo:g}{u} and {hi:g}{u}.")
    return v


# --------------------------------------------------------------------------- shapes


def sphere(diameter: float = 0.2, n_circ: int = 144) -> Body:
    d = _check("The diameter", diameter, SIZE_RANGE)
    R = d / 2
    phi = np.linspace(0, np.pi, n_circ // 2 + 1)  # pole to pole
    x = R * (1 - np.cos(phi))
    r = R * np.sin(phi)
    r[0] = r[-1] = 0.0
    V, F = revolve(x, r, n_circ)
    return Body(V, F, "sphere", f"Sphere, diameter {d:g} m", "bluff-smooth", np.pi * R**2, d, {"diameter": d},
                "A smooth ball: the classic bluff body. Textbook CD about 0.47 below the drag crisis "
                "(Re below about 3 x 10^5), about 0.1-0.2 above it.")


def cylinder(diameter: float = 0.15, length: float = 0.45, n_circ: int = 144, n_axial: int = 24) -> Body:
    """A circular cylinder lying across the wind (axis along y) with flat end caps."""
    d = _check("The diameter", diameter, SIZE_RANGE)
    L = _check("The length", length, SIZE_RANGE)
    if not CYLINDER_LENGTH_RANGE[0] * d <= L <= CYLINDER_LENGTH_RANGE[1] * d:
        raise ValueError(f"The cylinder's length must be between {CYLINDER_LENGTH_RANGE[0]:g} and "
                         f"{CYLINDER_LENGTH_RANGE[1]:g} times its diameter.")
    R = d / 2
    # profile along the axis (built along x, then turned so the axis is y): cap, side, cap
    n_cap = max(4, n_circ // 24)
    cap_r = np.linspace(0, R, n_cap + 1)
    x = np.concatenate([np.zeros(n_cap), np.linspace(0, L, n_axial + 1), np.full(n_cap, L)])
    r = np.concatenate([cap_r[:-1], np.full(n_axial + 1, R), cap_r[:-1][::-1]])
    V, F = revolve(x, r, n_circ)
    # axis x -> y: (x, y, z) -> (y, x, z) with y centred, then the body's own x (the former y) from 0
    V = np.column_stack([V[:, 1] + R, V[:, 0] - L / 2, V[:, 2]])
    F = _outward(V, F)
    return Body(V, F, "cylinder", f"Cylinder across the wind, diameter {d:g} m, length {L:g} m", "bluff-smooth",
                d * L, d,
                {"diameter": d, "length": L},
                "A round bar across the wind with flat ends. Textbook CD about 1.0-1.2 for a very long cylinder "
                "(Re 10^4 to 2 x 10^5); a short one with free ends has less, about 0.6-0.8 at length/diameter 2-5.")


def plate(height: float = 0.2, width: float = 0.2) -> Body:
    """A thin flat plate standing normal to the wind (thickness 5 % of the height)."""
    h = _check("The height", height, SIZE_RANGE)
    w = _check("The width", width, SIZE_RANGE)
    t = PLATE_THICKNESS * h
    V, F = box(t, w, h)
    return Body(V, F, "plate", f"Flat plate facing the wind, {w:g} m wide, {h:g} m high", "bluff", w * h, h,
                {"height": h, "width": w},
                "A flat plate square to the wind, the bluntest shape there is. Textbook CD about 1.1-1.2 for a "
                "square plate, rising towards 2 for a very long strip.")


def cube(side: float = 0.2) -> Body:
    s = _check("The side", side, SIZE_RANGE)
    V, F = box(s, s, s)
    return Body(V, F, "cube", f"Cube, side {s:g} m", "bluff", s * s, s, {"side": s},
                "A cube facing the wind squarely. Textbook CD about 1.05.")


def teardrop(diameter: float = 0.2, fineness: float = 3.0, n_circ: int = 120, n_axial: int = 120) -> Body:
    """A streamlined body of revolution: the NACA 00xx thickness distribution revolved about x,
    scaled to the given maximum diameter and length = fineness x diameter."""
    d = _check("The diameter", diameter, SIZE_RANGE)
    f = _check("The fineness ratio (length / diameter)", fineness, FINENESS_RANGE, "")
    L = f * d
    beta = np.linspace(0.0, np.pi, n_axial + 1)
    xi = 0.5 * (1.0 - np.cos(beta))  # cosine spacing, 0 ... 1
    a0, a1, a2, a3, a4 = _THICKNESS_COEFFS
    yt = a0 * np.sqrt(xi) + a1 * xi + a2 * xi**2 + a3 * xi**3 + a4 * xi**4
    yt[0] = yt[-1] = 0.0
    r = (d / 2) * yt / yt.max()
    V, F = revolve(L * xi, r, n_circ)
    return Body(V, F, "teardrop", f"Streamlined teardrop, diameter {d:g} m, fineness ratio {f:g}", "streamlined-body",
                np.pi * (d / 2) ** 2, d, {"diameter": d, "fineness": f},
                "A streamlined body of revolution (nose like an airship, tail tapering to a point). Textbook CD on "
                "frontal area about 0.05-0.1 for fineness ratios 3-4.")


# --------------------------------------------------------------------------- catalogue


SHAPES: dict[str, dict] = {
    "sphere": {"label": "Sphere", "make": sphere, "class": "bluff-smooth",
               "params": [{"id": "diameter", "label": "Diameter (m)", "default": 0.2, "min": SIZE_RANGE[0],
                           "max": SIZE_RANGE[1], "step": 0.01}]},
    "cylinder": {"label": "Cylinder across the wind (with end caps)", "make": cylinder, "class": "bluff-smooth",
                 "params": [{"id": "diameter", "label": "Diameter (m)", "default": 0.15, "min": SIZE_RANGE[0],
                             "max": SIZE_RANGE[1], "step": 0.01},
                            {"id": "length", "label": "Length across the wind (m)", "default": 0.45,
                             "min": SIZE_RANGE[0], "max": SIZE_RANGE[1], "step": 0.01}]},
    "plate": {"label": "Flat plate facing the wind", "make": plate, "class": "bluff",
              "params": [{"id": "height", "label": "Height (m)", "default": 0.2, "min": SIZE_RANGE[0],
                          "max": SIZE_RANGE[1], "step": 0.01},
                         {"id": "width", "label": "Width (m)", "default": 0.2, "min": SIZE_RANGE[0],
                          "max": SIZE_RANGE[1], "step": 0.01}]},
    "cube": {"label": "Cube", "make": cube, "class": "bluff",
             "params": [{"id": "side", "label": "Side (m)", "default": 0.2, "min": SIZE_RANGE[0],
                         "max": SIZE_RANGE[1], "step": 0.01}]},
    "teardrop": {"label": "Streamlined teardrop (body of revolution)", "make": teardrop, "class": "streamlined-body",
                 "params": [{"id": "diameter", "label": "Maximum diameter (m)", "default": 0.2, "min": SIZE_RANGE[0],
                             "max": SIZE_RANGE[1], "step": 0.01},
                            {"id": "fineness", "label": "Fineness ratio (length ÷ diameter)", "default": 3.0,
                             "min": FINENESS_RANGE[0], "max": FINENESS_RANGE[1], "step": 0.5}]},
}


def make_body(shape: str, **params) -> Body:
    """Build a catalogue shape from its parameters (unknown parameters are refused)."""
    spec = SHAPES.get(shape)
    if spec is None:
        raise ValueError("Choose one of the built-in shapes: " + ", ".join(SHAPES))
    allowed = {p["id"] for p in spec["params"]}
    extra = set(params) - allowed
    if extra:
        raise ValueError(f"Unknown setting for a {spec['label'].lower()}: {', '.join(sorted(extra))}.")
    return spec["make"](**params)


def catalogue() -> list[dict]:
    """The shapes and their parameters, for the page's form (no callables)."""
    return [{"id": sid, "label": s["label"], "class": s["class"], "params": s["params"],
             "description": s["make"]().description} for sid, s in SHAPES.items()]


__all__ = ["Body", "SHAPES", "box", "catalogue", "cube", "cylinder", "make_body", "plate", "revolve", "sphere",
           "teardrop"]
