"""Pictures for the report without matplotlib: a numpy triangle rasteriser, a PNG encoder, and the
two renderings the report needs (the pressure-coloured 3D surface and the mid-span flow slice).

Everything here is pure numpy + zlib. Triangles are drawn in the order given (painter's
algorithm: the caller sorts far-to-near); per-vertex colours are interpolated with
barycentric weights. Tiny triangles (bounding box within one pixel) are drawn as single
pixels in one vectorised pass, which is most of a decimated CFD surface.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

# the same colour stops as windlab/app/static/plot.js (viridis; ColorBrewer RdBu reversed)
VIRIDIS = ["#440154", "#472d7b", "#3b528b", "#2c728e", "#21918c", "#28ae80", "#5ec962", "#addc30", "#fde725"]
DIVERGING = ["#2166ac", "#4393c3", "#92c5de", "#d1e5f0", "#f7f7f7", "#fddbc7", "#f4a582", "#d6604d", "#b2182b"]
BODY_RGB = (0.24, 0.24, 0.23)
BACKGROUND_RGB = (0.99, 0.99, 0.98)


def hex_rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)])


def colormap(t: np.ndarray, stops: list[str]) -> np.ndarray:
    """Colours (n, 3) in 0..1 at t in [0, 1] (clamped) along the hex stops (linear, like plot.js)."""
    rgb = np.array([hex_rgb(s) for s in stops])
    x = np.clip(np.nan_to_num(np.asarray(t, dtype=float)), 0.0, 1.0) * (len(stops) - 1)
    i = np.minimum(len(stops) - 2, np.floor(x).astype(int))
    f = (x - i)[:, None]
    return rgb[i] * (1 - f) + rgb[i + 1] * f


def nice_ceil(x: float) -> float:
    if not x > 0:
        return 0.0
    mag = 10 ** np.floor(np.log10(x))
    for k in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if k * mag >= x * (1 - 1e-9):
            return float(f"{k * mag:.6g}")
    return float(10 * mag)


def speed_range(values: np.ndarray) -> tuple[float, float]:
    """0 .. a nice value just above the 99th percentile (plot.js speedRange)."""
    s = np.sort(np.asarray(values, dtype=float))
    p99 = s[min(len(s) - 1, round(0.99 * (len(s) - 1)))] if len(s) else 1.0
    return 0.0, max(0.5, nice_ceil(float(p99)))


def cp_symmetric_range(values: np.ndarray) -> tuple[float, float]:
    """+/- a nice value covering the 2nd..98th percentiles (plot.js cpSymmetricRange)."""
    s = np.sort(np.asarray(values, dtype=float))
    if not len(s):
        return -1.0, 1.0
    q = lambda f: s[min(len(s) - 1, round(f * (len(s) - 1)))]  # noqa: E731
    m = max(0.2, nice_ceil(max(abs(float(q(0.02))), abs(float(q(0.98))))))
    return -m, m


# ----------------------------------------------------------------------------- drawing


def new_canvas(width: int, height: int, rgb=BACKGROUND_RGB) -> np.ndarray:
    c = np.empty((int(height), int(width), 3), dtype=float)
    c[:] = rgb
    return c


def draw_triangles(canvas: np.ndarray, xy: np.ndarray, colours: np.ndarray) -> None:
    """Fill triangles ``xy`` (T, 3, 2) in pixel coordinates (x right, y down) with per-vertex
    ``colours`` (T, 3, 3) in 0..1, in the given order."""
    h, w = canvas.shape[:2]
    xy = np.asarray(xy, dtype=float)
    colours = np.asarray(colours, dtype=float)
    if len(xy) == 0:
        return
    lo = np.floor(xy.min(axis=1)).astype(int)
    hi = np.floor(xy.max(axis=1)).astype(int)
    inside = (hi[:, 0] >= 0) & (hi[:, 1] >= 0) & (lo[:, 0] < w) & (lo[:, 1] < h)
    span = hi - lo
    tiny = inside & (span[:, 0] <= 0) & (span[:, 1] <= 0)
    big = inside & ~tiny
    if tiny.any():  # one pixel each, mean colour; later triangles overwrite earlier ones
        px = np.clip(lo[tiny], 0, [w - 1, h - 1])
        canvas[px[:, 1], px[:, 0]] = colours[tiny].mean(axis=1)
    for t in np.flatnonzero(big):
        x0, y0 = max(lo[t, 0], 0), max(lo[t, 1], 0)
        x1, y1 = min(hi[t, 0], w - 1), min(hi[t, 1], h - 1)
        if x1 < x0 or y1 < y0:
            continue
        (ax, ay), (bx, by), (cx, cy) = xy[t]
        det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(det) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        l1 = ((bx - gx) * (cy - gy) - (cx - gx) * (by - gy)) / det
        l2 = ((cx - gx) * (ay - gy) - (ax - gx) * (cy - gy)) / det
        l3 = 1.0 - l1 - l2
        eps = -1e-9
        mask = (l1 >= eps) & (l2 >= eps) & (l3 >= eps)
        if not mask.any():  # a sliver that covers no pixel centre: mark its centroid pixel
            cxp, cyp = int(xy[t, :, 0].mean()), int(xy[t, :, 1].mean())
            if 0 <= cxp < w and 0 <= cyp < h:
                canvas[cyp, cxp] = colours[t].mean(axis=0)
            continue
        col = (l1[..., None] * colours[t, 0] + l2[..., None] * colours[t, 1] + l3[..., None] * colours[t, 2])
        region = canvas[y0:y1 + 1, x0:x1 + 1]
        region[mask] = np.clip(col[mask], 0.0, 1.0)


def draw_polyline(canvas: np.ndarray, pts: np.ndarray, rgb, step: float = 0.5) -> None:
    """A thin line through ``pts`` (n, 2) in pixel coordinates."""
    h, w = canvas.shape[:2]
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        return
    seg = np.diff(pts, axis=0)
    length = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(length)])
    if cum[-1] <= 0:
        return
    s = np.arange(0.0, cum[-1], step)
    x = np.interp(s, cum, pts[:, 0])
    y = np.interp(s, cum, pts[:, 1])
    ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
    ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    canvas[iy[ok], ix[ok]] = rgb


def png_bytes(canvas: np.ndarray) -> bytes:
    """An RGB PNG of a float canvas (h, w, 3) in 0..1."""
    rgb = (np.clip(canvas, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    h, w = rgb.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), np.uint8), rgb.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ----------------------------------------------------------------------------- the two pictures

MAX_PICTURE_TRIANGLES = 60_000


def render_slice(sl: dict, window: list[float] | None, width: int = 720, streamlines=None,
                 field: str = "speed") -> dict:
    """The mid-span flow slice (decoded blob of windlab.app.slice) coloured by air speed (or Cp) in a
    window [x0, x1, z0, z1]; pixels no triangle covers show the part. Returns {png, lo, hi, window}."""
    from windlab.app.surface import decimate

    xz, tris = np.asarray(sl["xz"], float), np.asarray(sl["triangles"])
    vals = np.asarray(sl["speed"] if field == "speed" else sl["cp"], float)
    if window is None:
        window = [sl["x_min"], sl["x_max"], sl["z_min"], sl["z_max"]]
    x0, x1, z0, z1 = (float(v) for v in window)
    if len(tris) > MAX_PICTURE_TRIANGLES:
        xz, tris, vals = decimate(np.column_stack([xz[:, 0], np.zeros(len(xz)), xz[:, 1]]), tris, vals,
                                  MAX_PICTURE_TRIANGLES)
        xz = xz[:, [0, 2]]
    scale = width / max(x1 - x0, 1e-9)
    height = max(40, int(round((z1 - z0) * scale)))
    canvas = new_canvas(width, height, BODY_RGB)
    inwin = np.any(((xz[tris, 0] >= x0) & (xz[tris, 0] <= x1) & (xz[tris, 1] >= z0) & (xz[tris, 1] <= z1)), axis=1)
    tris = tris[inwin]
    lo, hi = speed_range(vals) if field == "speed" else cp_symmetric_range(vals)
    col = colormap((vals - lo) / (hi - lo), VIRIDIS if field == "speed" else DIVERGING)
    px = np.column_stack([(xz[:, 0] - x0) * scale, (z1 - xz[:, 1]) * scale])
    draw_triangles(canvas, px[tris], col[tris])
    for line in streamlines or []:
        pts = np.asarray(line, float)
        if pts.ndim == 2 and len(pts) >= 2:
            draw_polyline(canvas, np.column_stack([(pts[:, 0] - x0) * scale, (z1 - pts[:, 1]) * scale]),
                          (0.07, 0.07, 0.06))
    return {"png": png_bytes(canvas), "lo": lo, "hi": hi, "window": [x0, x1, z0, z1], "width": width,
            "height": height}


def render_surface(surf: dict, width: int = 720, view=(-1.0, -1.6, 1.0), light=(-0.4, -0.5, 0.75)) -> dict:
    """The part seen from the front-left-above, coloured by Cp (diverging map, symmetric range) with
    flat Lambert shading. ``surf`` is a decoded windlab.app.surface blob. Returns {png, lo, hi, ...}."""
    from windlab.app.surface import decimate

    verts, tris = np.asarray(surf["vertices"], float), np.asarray(surf["triangles"])
    cp = surf.get("cp")
    cp = None if cp is None else np.asarray(cp, float)
    if len(tris) > MAX_PICTURE_TRIANGLES:
        verts, tris, cp = decimate(verts, tris, cp, MAX_PICTURE_TRIANGLES)
    f = -np.asarray(view, float)
    f /= np.linalg.norm(f)  # from the eye into the scene
    right = np.cross(f, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, f)
    u, v, depth = verts @ right, verts @ up, verts @ f
    margin = 0.06
    span_u, span_v = max(u.max() - u.min(), 1e-9), max(v.max() - v.min(), 1e-9)
    scale = width * (1 - 2 * margin) / span_u
    height = max(60, int(round(span_v * scale / (1 - 2 * margin))))
    px = np.column_stack([(u - u.min()) * scale + margin * width, (v.max() - v) * scale + margin * height])
    order = np.argsort(-depth[tris].mean(axis=1))  # far first
    tris = tris[order]
    n = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    lit = np.asarray(light, float)
    lit /= np.linalg.norm(lit)
    shade = 0.62 + 0.38 * np.abs(n @ lit)
    if cp is not None and len(cp) == len(verts):
        lo, hi = cp_symmetric_range(cp)
        col = colormap((cp - lo) / (hi - lo), DIVERGING)
    else:
        lo, hi = 0.0, 0.0
        col = np.tile(np.array([0.72, 0.74, 0.78]), (len(verts), 1))
    canvas = new_canvas(width, height)
    draw_triangles(canvas, px[tris], col[tris] * shade[:, None, None])
    return {"png": png_bytes(canvas), "lo": lo, "hi": hi, "width": width, "height": height}


__all__ = ["DIVERGING", "VIRIDIS", "colormap", "draw_polyline", "draw_triangles", "new_canvas", "png_bytes",
           "render_slice", "render_surface"]
