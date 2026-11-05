"""Quarter-turn rotations of a stored part (SolidWorks parts come with arbitrary axes).

WindLab's frame: the wind blows along +x, z is up, y is across the wind (span).
Only rotations by multiples of 90 degrees about x, y or z are offered: they are exact
(coordinates are just swapped and negated), so rotating four times gives back the
identical file.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from windlab.geometry.stl import _STL_DTYPE

AXES = {"x": 0, "y": 1, "z": 2}


def quarter_turn(axis: str, turns: int = 1) -> np.ndarray:
    """Right-handed rotation by ``turns`` x 90 degrees about ``axis`` (integer matrix)."""
    if axis not in AXES:
        raise ValueError("axis must be x, y or z")
    i = AXES[axis]
    j, k = (i + 1) % 3, (i + 2) % 3
    r = np.eye(3, dtype=np.int64)
    r[j, j], r[j, k], r[k, j], r[k, k] = 0, -1, 1, 0  # +90 deg: j -> k, k -> -j
    return np.linalg.matrix_power(r, int(turns) % 4)


def is_quarter_turn_rotation(r) -> bool:
    r = np.asarray(r)
    return (r.shape == (3, 3) and set(np.unique(r)) <= {-1, 0, 1} and np.all(np.abs(r).sum(axis=0) == 1)
            and np.all(np.abs(r).sum(axis=1) == 1) and round(float(np.linalg.det(r))) == 1)


def suggest_rotation(size) -> np.ndarray:
    """Quarter-turn rotation putting the longest side along the wind (x) and the shortest
    up (z), so a flat part lies flat in the wind. Ties keep the current order."""
    size = np.asarray(size, dtype=float)
    order = sorted(range(3), key=lambda a: (-size[a], a))  # longest, middle, shortest
    r = np.zeros((3, 3), dtype=np.int64)
    for new_axis, old_axis in enumerate(order):
        r[new_axis, old_axis] = 1
    if round(np.linalg.det(r)) < 0:  # keep it a rotation (not a mirror image): flip y
        r[1] *= -1
    return r


def looks_like_a_wing(size) -> bool:
    """Flat and longer one way than the other: then the long side is probably the span."""
    a, b, c = sorted(np.asarray(size, dtype=float), reverse=True)
    return c < 0.3 * b and a > 1.5 * b


def read_records(path: Path) -> np.ndarray:
    data = Path(path).read_bytes()
    if len(data) < 84:
        raise ValueError("geometry file is damaged")
    n = int.from_bytes(data[80:84], "little")
    if len(data) != 84 + 50 * n:
        raise ValueError("geometry file is damaged")
    return np.frombuffer(data, dtype=_STL_DTYPE, count=n, offset=84).copy(), data[:80]


def rotate_stl(path: Path, r: np.ndarray) -> tuple[tuple, tuple]:
    """Rotate a WindLab binary STL in place (atomic replace). Returns the new bounds."""
    if not is_quarter_turn_rotation(r):
        raise ValueError("only quarter-turn rotations are allowed")
    rec, header = read_records(path)
    rf = np.asarray(r, dtype=np.float32)
    for key in ("normal", "v0", "v1", "v2"):
        # exact: each new coordinate is +/- one old coordinate (+ 0.0 turns -0.0 into 0.0)
        rec[key] = rec[key] @ rf.T + np.float32(0.0)
    pts = np.concatenate([rec["v0"], rec["v1"], rec["v2"]]).astype(np.float64)
    tmp = Path(path).with_name(Path(path).name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(header)
        fh.write(np.uint32(len(rec)).tobytes())
        fh.write(rec.tobytes())
    os.replace(tmp, path)
    return tuple(pts.min(axis=0).tolist()), tuple(pts.max(axis=0).tolist())
