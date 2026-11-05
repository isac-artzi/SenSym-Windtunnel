"""Mid-span flow slice for the results page.

Source: the ``midPlane`` function object of the external_aero template (OpenFOAM's
``surfaces`` function object with a ``cuttingPlane`` surface at y = centre of the body,
``interpolate true``), written as VTK PolyData with point values of U and p. Only
WindLab's own solver output is read.

The browser gets a little-endian binary blob:

    b"WLP1", uint32 n_points, uint32 n_triangles,
    float32 x_min, x_max, z_min, z_max, speed_max, cp_min, cp_max, y_plane,
    float32 xz[2 n_points] (metres), uint32 triangles[3 n_triangles],
    float32 speed[n_points] (|U| / U_inf), float32 cp[n_points] (p / (0.5 U_inf^2))
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import numpy as np

from windlab.app import flowlines
from windlab.app.surface import SurfaceError, _decode_array, decimate, triangulate

MAGIC = b"WLP1"
MAX_TRIANGLES = 120_000

_PIECE = re.compile(rb"<Piece\s+NumberOfPoints='(\d+)'\s+NumberOfPolys='(\d+)'")
_SECTION = re.compile(rb"<(PointData|CellData|Points|Polys)\b[^>]*>(.*?)</\1>", re.S)
_ARRAY = re.compile(rb"<DataArray\s+([^>]*)>\s*([A-Za-z0-9+/=\s]*?)\s*</DataArray>", re.S)
_ATTR = re.compile(rb"(\w+)='([^']*)'")


def read_vtp_sections(path: Path) -> dict[str, dict[str, np.ndarray]]:
    """Arrays of a single-piece VTK PolyData file, grouped by section."""
    data = Path(path).read_bytes()
    m = _PIECE.search(data)
    if not m:
        raise SurfaceError("no PolyData piece in slice file")
    n_points, n_polys = int(m.group(1)), int(m.group(2))
    out: dict[str, dict[str, np.ndarray]] = {}
    for sm in _SECTION.finditer(data, m.end()):
        arrays = {}
        for am in _ARRAY.finditer(sm.group(2)):
            attrs = dict(_ATTR.findall(am.group(1)))
            arr = _decode_array(attrs, am.group(2))
            ncomp = int(attrs.get(b"NumberOfComponents", b"1"))
            arrays[attrs.get(b"Name", b"").decode()] = arr.reshape(-1, ncomp) if ncomp > 1 else arr
        out[sm.group(1).decode()] = arrays
    pts = out.get("Points", {}).get("Points")
    if pts is None or pts.shape != (n_points, 3):
        raise SurfaceError("slice file has no usable points")
    if len(out.get("Polys", {}).get("offsets", ())) != n_polys:
        raise SurfaceError("slice file has no usable polygons")
    return out


def latest_slice_file(case: Path) -> Path | None:
    base = Path(case) / "postProcessing" / "midPlane"
    if not base.is_dir():
        return None
    best = None
    for d in base.iterdir():
        try:
            t = float(d.name)
        except ValueError:
            continue
        f = d / "plane.vtp"
        if f.is_file() and not f.is_symlink() and (best is None or t > best[0]):
            best = (t, f)
    return best[1] if best else None


def _point_values(sec: dict, name: str, n_points: int, tris: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Point values of a field; cell values are averaged to the points if that's all there is."""
    pd = sec.get("PointData", {})
    if name in pd and len(pd[name]) == n_points:
        return np.asarray(pd[name], dtype=np.float64)
    cd = sec.get("CellData", {})
    if name in cd:
        v = np.asarray(cd[name], dtype=np.float64)[poly]
        v = v.reshape(len(v), -1)
        num = np.zeros((n_points, v.shape[1]))
        cnt = np.zeros(n_points)
        for c in range(3):
            np.add.at(num, tris[:, c], v)
            np.add.at(cnt, tris[:, c], 1)
        out = num / np.maximum(cnt, 1)[:, None]
        return out[:, 0] if out.shape[1] == 1 else out
    raise SurfaceError(f"the slice has no {name} values")


def read_slice(case: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(points (n, 3), triangles (m, 3), U (n, 3), p (n,)) of the latest mid-plane slice."""
    f = latest_slice_file(case)
    if f is None:
        raise SurfaceError("the solver did not write a flow slice")
    sec = read_vtp_sections(f)
    pts = sec["Points"]["Points"].astype(np.float64)
    tris, poly = triangulate(sec["Polys"]["connectivity"], sec["Polys"]["offsets"])
    U = _point_values(sec, "U", len(pts), tris, poly)
    p = _point_values(sec, "p", len(pts), tris, poly)
    if U.ndim != 2 or U.shape[1] != 3:
        raise SurfaceError("the slice velocity is not a vector")
    return pts, tris, U, p


def flow_slice(case: Path, airspeed: float, max_triangles: int = MAX_TRIANGLES,
               data: tuple | None = None) -> bytes:
    pts, tris, U, p = data if data is not None else read_slice(case)
    speed = np.linalg.norm(U, axis=1) / airspeed
    cp = p / (0.5 * airspeed**2)
    vals = np.column_stack([speed, cp])
    pts, tris, vals = decimate(pts, tris, vals, max_triangles)
    return encode(pts, tris, vals[:, 0], vals[:, 1])


def slice_info(case_meta: dict, extent: list[float], y: float, data: tuple | None = None,
               airspeed: float | None = None) -> dict:
    """Viewing windows, the body box and (with ``data``) streamlines for the page.

    ``case_meta`` is the case's windlab_case.json; ``extent`` = [x0, x1, z0, z1] of the slice.
    """
    body_min, body_max = body_box(case_meta)
    closeup = flowlines.clip_window(flowlines.closeup_window(body_min, body_max), extent)
    tunnel = [float(v) for v in extent]
    size = max(float(np.max(np.subtract(body_max, body_min))), 1e-6)
    y_centre = 0.5 * (body_min[1] + body_max[1])
    info = {
        "y": float(y),
        "y_centre": float(y_centre),
        "body_min": [float(v) for v in body_min],
        "body_max": [float(v) for v in body_max],
        "body_size": size,
        "windows": {"closeup": closeup, "tunnel": tunnel},
        "streamlines": None,
    }
    if data is not None and airspeed:
        pts, tris, U, _p = data
        uw = U[:, [0, 2]] / float(airspeed)
        xz = pts[:, [0, 2]]
        info["streamlines"] = {name: flowlines.streamlines(xz, tris, uw, win)
                               for name, win in info["windows"].items()}
    return info


def body_box(case_meta: dict) -> tuple[list[float], list[float]]:
    """The part's bounding box: stored by newer case builders, else recovered from the
    slice bounds of the older template (0.5 L ahead, 1.5 L behind, 0.6 L above / below)."""
    if "body_min" in case_meta and "body_max" in case_meta:
        return list(case_meta["body_min"]), list(case_meta["body_max"])
    (sx0, sy0, sz0), (sx1, sy1, sz1) = case_meta["slice_bounds"]
    L = float(case_meta["l_ref"])
    yc = float(case_meta.get("slice_point", [0, 0.5 * (sy0 + sy1), 0])[1])
    return [sx0 + 0.5 * L, yc, sz0 + 0.6 * L], [sx1 - 1.5 * L, yc, sz1 - 0.6 * L]


def encode(pts: np.ndarray, tris: np.ndarray, speed: np.ndarray, cp: np.ndarray) -> bytes:
    xz = np.ascontiguousarray(pts[:, [0, 2]], dtype="<f4")
    y = float(np.mean(pts[:, 1])) if len(pts) else 0.0
    head = MAGIC + struct.pack(
        "<II8f", len(pts), len(tris), float(xz[:, 0].min()), float(xz[:, 0].max()), float(xz[:, 1].min()),
        float(xz[:, 1].max()), float(np.max(speed)), float(np.min(cp)), float(np.max(cp)), y)
    return (head + xz.tobytes() + np.ascontiguousarray(tris, dtype="<u4").tobytes()
            + np.ascontiguousarray(speed, dtype="<f4").tobytes() + np.ascontiguousarray(cp, dtype="<f4").tobytes())


def decode(blob: bytes) -> dict:
    if blob[:4] != MAGIC:
        raise SurfaceError("not a WindLab slice")
    nv, nt, *f = struct.unpack("<II8f", blob[4:44])
    off = 44
    xz = np.frombuffer(blob, "<f4", 2 * nv, off).reshape(nv, 2)
    off += 8 * nv
    tris = np.frombuffer(blob, "<u4", 3 * nt, off).reshape(nt, 3)
    off += 12 * nt
    speed = np.frombuffer(blob, "<f4", nv, off)
    off += 4 * nv
    cp = np.frombuffer(blob, "<f4", nv, off)
    keys = ("x_min", "x_max", "z_min", "z_max", "speed_max", "cp_min", "cp_max", "y")
    return {"xz": xz, "triangles": tris, "speed": speed, "cp": cp, **dict(zip(keys, f, strict=True))}
