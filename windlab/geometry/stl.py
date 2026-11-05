"""Minimal binary STL writer (no third-party dependencies)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

_STL_DTYPE = np.dtype(
    [("normal", "<f4", 3), ("v0", "<f4", 3), ("v1", "<f4", 3), ("v2", "<f4", 3), ("attr", "<u2")]
)


def write_binary_stl(path, vertices: np.ndarray, faces: np.ndarray, solid_name: str = "body") -> None:
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    v0, v1, v2 = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, norm, out=np.zeros_like(n), where=norm > 0)

    data = np.zeros(len(faces), dtype=_STL_DTYPE)
    data["normal"], data["v0"], data["v1"], data["v2"] = n, v0, v1, v2
    # Must not start with "solid": readers would then treat the file as ASCII STL.
    header = f"WindLab binary STL: {solid_name}".encode("ascii")[:80].ljust(80, b" ")
    path = Path(path)
    with path.open("wb") as fh:
        fh.write(header)
        fh.write(np.uint32(len(faces)).tobytes())
        fh.write(data.tobytes())
