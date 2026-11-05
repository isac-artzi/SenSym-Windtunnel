"""Body surface with pressure coefficient, for the 3D view in the browser.

Source: the ``bodySurface`` function object of the external_aero template, which writes
the body patch with cell values of ``p`` as VTK XML PolyData (``.vtp``, base64 binary
arrays with a UInt64 length header). Only WindLab's own solver output is read here.

Cp = p / (0.5 U^2): simpleFoam's ``p`` is kinematic (pressure / density) and the outlet
holds p = 0, so freestream static pressure is ~0 (to within the small far-field drift).

The browser gets a compact little-endian binary blob (see :func:`encode`):

    b"WLS1", uint32 n_vertices, uint32 n_triangles, uint32 flags (1 = has Cp),
    float32 cp_min, float32 cp_max, float32 positions[3 n_vertices] (metres, x y z),
    uint32 triangles[3 n_triangles], float32 cp[n_vertices] (if flags & 1)

Cp is stored per vertex (area-weighted average of the faces around it) for smooth
colouring. Large surfaces are decimated by vertex clustering on a uniform grid.
"""

from __future__ import annotations

import base64
import re
import struct
from pathlib import Path

import numpy as np

MAX_TRIANGLES = 150_000
MAGIC = b"WLS1"

_PIECE = re.compile(rb"<Piece\s+NumberOfPoints='(\d+)'\s+NumberOfPolys='(\d+)'")
_ARRAY = re.compile(rb"<DataArray\s+([^>]*)>\s*([A-Za-z0-9+/=\s]*?)\s*</DataArray>", re.S)
_ATTR = re.compile(rb"(\w+)='([^']*)'")
_TYPES = {b"Float32": "<f4", b"Float64": "<f8", b"Int32": "<i4", b"Int64": "<i8", b"UInt32": "<u4", b"UInt64": "<u8"}


class SurfaceError(RuntimeError):
    pass


def _decode_array(attrs: dict[bytes, bytes], payload: bytes) -> np.ndarray:
    dtype = _TYPES.get(attrs.get(b"type", b""))
    if dtype is None or attrs.get(b"format") != b"binary":
        raise SurfaceError("unsupported VTK array")
    raw = base64.b64decode(b"".join(payload.split()))
    if len(raw) < 8:
        raise SurfaceError("truncated VTK array")
    n = struct.unpack("<Q", raw[:8])[0]
    if n > len(raw) - 8:
        raise SurfaceError("truncated VTK array")
    return np.frombuffer(raw[8 : 8 + n], dtype=dtype)


def read_vtp(path: Path) -> dict[str, np.ndarray]:
    """Points, connectivity, offsets and cell arrays of a single-piece VTK PolyData file."""
    data = Path(path).read_bytes()
    m = _PIECE.search(data)
    if not m:
        raise SurfaceError("no PolyData piece in surface file")
    n_points, n_polys = int(m.group(1)), int(m.group(2))
    out: dict[str, np.ndarray] = {}
    for am in _ARRAY.finditer(data, m.end()):
        attrs = dict(_ATTR.findall(am.group(1)))
        name = attrs.get(b"Name", b"").decode()
        arr = _decode_array(attrs, am.group(2))
        ncomp = int(attrs.get(b"NumberOfComponents", b"1"))
        out[name] = arr.reshape(-1, ncomp) if ncomp > 1 else arr
    pts = out.get("Points")
    if pts is None or pts.shape != (n_points, 3):
        raise SurfaceError("surface file has no usable points")
    if len(out.get("offsets", ())) != n_polys:
        raise SurfaceError("surface file has no usable polygons")
    return out


def triangulate(connectivity: np.ndarray, offsets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fan-triangulate polygons. Returns (triangles (T, 3), index of the source polygon (T,))."""
    offsets = np.asarray(offsets, dtype=np.int64)
    starts = np.concatenate([[0], offsets[:-1]])
    sizes = offsets - starts
    if np.any(sizes < 3):
        raise SurfaceError("surface file has a polygon with fewer than 3 corners")
    n_tri = sizes - 2
    poly = np.repeat(np.arange(len(sizes)), n_tri)
    k = np.arange(n_tri.sum()) - np.repeat(np.cumsum(n_tri) - n_tri, n_tri)  # 0 .. n-3 within each polygon
    first = starts[poly]
    conn = np.asarray(connectivity, dtype=np.int64)
    tris = np.column_stack([conn[first], conn[first + k + 1], conn[first + k + 2]])
    return tris, poly


def vertex_average(n_vertices: int, tris: np.ndarray, verts: np.ndarray, face_values: np.ndarray) -> np.ndarray:
    """Area-weighted average of face values at each vertex."""
    a = np.linalg.norm(np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]]), axis=1)
    a = np.maximum(a, 1e-30)
    num = np.zeros(n_vertices)
    den = np.zeros(n_vertices)
    for c in range(3):
        np.add.at(num, tris[:, c], a * face_values)
        np.add.at(den, tris[:, c], a)
    return np.divide(num, den, out=np.zeros(n_vertices), where=den > 0)


def decimate(verts: np.ndarray, tris: np.ndarray, values: np.ndarray | None, max_triangles: int = MAX_TRIANGLES):
    """Vertex clustering on a uniform grid until at most ``max_triangles`` remain."""
    if len(tris) <= max_triangles:
        return verts, tris, values
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    ext = float(np.max(hi - lo)) or 1.0
    cells = int(np.sqrt(max_triangles / 2) * 1.2)
    for _ in range(12):
        h = ext / cells
        key = np.floor((verts - lo) / h).astype(np.int64)
        _, cluster = np.unique(key, axis=0, return_inverse=True)
        cluster = cluster.reshape(-1)
        t = cluster[tris]
        t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 2] != t[:, 0])]  # drop collapsed
        if len(t) <= max_triangles:
            break
        cells = int(cells * 0.8)
    n = int(cluster.max()) + 1
    count = np.bincount(cluster, minlength=n).astype(float)
    new_v = np.column_stack([np.bincount(cluster, weights=verts[:, c], minlength=n) for c in range(3)]) / count[:, None]
    if values is None:
        new_vals = None
    elif np.ndim(values) == 2:  # several values per vertex
        new_vals = np.column_stack([np.bincount(cluster, weights=values[:, k], minlength=n) / count
                                    for k in range(values.shape[1])])
    else:
        new_vals = np.bincount(cluster, weights=values, minlength=n) / count
    return new_v, t, new_vals


def encode(verts: np.ndarray, tris: np.ndarray, cp: np.ndarray | None) -> bytes:
    verts = np.ascontiguousarray(verts, dtype="<f4")
    tris = np.ascontiguousarray(tris, dtype="<u4")
    has_cp = cp is not None and len(cp) == len(verts)
    lo, hi = (float(np.min(cp)), float(np.max(cp))) if has_cp and len(cp) else (0.0, 0.0)
    head = MAGIC + struct.pack("<IIIff", len(verts), len(tris), 1 if has_cp else 0, lo, hi)
    body = verts.tobytes() + tris.tobytes()
    if has_cp:
        body += np.ascontiguousarray(cp, dtype="<f4").tobytes()
    return head + body


def decode(blob: bytes) -> dict:
    """Inverse of :func:`encode` (used by tests and the command-line check)."""
    if blob[:4] != MAGIC:
        raise SurfaceError("not a WindLab surface")
    nv, nt, flags, lo, hi = struct.unpack("<IIIff", blob[4:24])
    off = 24
    verts = np.frombuffer(blob, "<f4", 3 * nv, off).reshape(nv, 3)
    off += 12 * nv
    tris = np.frombuffer(blob, "<u4", 3 * nt, off).reshape(nt, 3)
    off += 12 * nt
    cp = np.frombuffer(blob, "<f4", nv, off) if flags & 1 else None
    return {"vertices": verts, "triangles": tris, "cp": cp, "cp_min": lo, "cp_max": hi}


def latest_surface_file(case: Path) -> Path | None:
    base = Path(case) / "postProcessing" / "bodySurface"
    if not base.is_dir():
        return None
    best = None
    for d in base.iterdir():
        try:
            t = float(d.name)
        except ValueError:
            continue
        f = d / "body.vtp"
        if f.is_file() and not f.is_symlink() and (best is None or t > best[0]):
            best = (t, f)
    return best[1] if best else None


def pressure_surface(case: Path, airspeed: float, max_triangles: int = MAX_TRIANGLES) -> bytes:
    """Read the solver's body surface and return the encoded mesh with per-vertex Cp."""
    f = latest_surface_file(case)
    if f is None:
        raise SurfaceError("the solver did not write a body surface")
    d = read_vtp(f)
    verts = d["Points"].astype(np.float64)
    tris, poly = triangulate(d["connectivity"], d["offsets"])
    if "p" not in d or len(d["p"]) != len(d["offsets"]):
        raise SurfaceError("the body surface has no pressure values")
    cp_face = d["p"].astype(np.float64)[poly] / (0.5 * airspeed**2)
    cp = vertex_average(len(verts), tris, verts, cp_face)
    verts, tris, cp = decimate(verts, tris, cp, max_triangles)
    return encode(verts, tris, cp)


def stl_surface(path: Path, max_triangles: int = MAX_TRIANGLES) -> bytes:
    """Encode a WindLab-written binary STL (metres) for the shape preview, without Cp."""
    data = Path(path).read_bytes()
    n = int.from_bytes(data[80:84], "little")
    if len(data) != 84 + 50 * n:
        raise SurfaceError("geometry file is damaged")
    rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n, offset=84)
    corners = rec["v"].reshape(-1, 3).astype(np.float64)
    keys, inverse = np.unique(corners, axis=0, return_inverse=True)
    tris = inverse.reshape(-1, 3)
    verts, tris, _ = decimate(keys, tris, None, max_triangles)
    return encode(verts, tris, None)
