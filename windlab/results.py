"""Read OpenFOAM logs and function-object output into plain Python data."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import numpy as np


def _read_dat(path: Path) -> dict[str, np.ndarray] | None:
    """Read a whitespace .dat file whose last '#' line holds the column names."""
    if not path.is_file():
        return None
    header, rows = None, []
    with path.open() as fh:
        for line in fh:
            if line.startswith("#"):
                header = line[1:].split()
            elif line.strip():
                rows.append(line.split())
    if header is None or not rows:
        return None
    width = len(header)
    rows = [r for r in rows if len(r) == width]  # drop a partially written last line
    cols = list(zip(*rows, strict=True))
    out = {}
    for name, col in zip(header, cols, strict=True):
        try:
            out[name] = np.array(col, dtype=float)
        except ValueError:
            out[name] = np.array(col)
    return out


def _latest_dir(base: Path) -> Path | None:
    if not base.is_dir():
        return None
    times = []
    for d in base.iterdir():
        with contextlib.suppress(ValueError):
            times.append((float(d.name), d))
    return max(times)[1] if times else None


def read_force_coeffs(case: Path) -> dict[str, np.ndarray] | None:
    """Concatenate forceCoeffs output across restarts (one folder per start time)."""
    base = Path(case) / "postProcessing" / "forceCoeffs1"
    if not base.is_dir():
        return None
    parts = []
    for d in sorted(base.iterdir(), key=lambda p: float(p.name) if _isnum(p.name) else -1):
        data = _read_dat(d / "coefficient.dat")
        if data:
            parts.append(data)
    if not parts:
        return None
    keys = [k for k in ("Time", "Cd", "Cl", "CmPitch", "Cs") if all(k in p for p in parts)]
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def _isnum(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def summarize_coeffs(c: dict[str, np.ndarray], window: int = 200) -> dict:
    """Final values as mean +/- half peak-to-peak over the trailing window (SIM-10)."""
    out = {}
    w = min(window, len(c["Time"]))
    for key, name in (("Cl", "cl"), ("Cd", "cd"), ("CmPitch", "cm")):
        if key not in c:
            continue
        tail = c[key][-w:]
        out[name] = float(tail.mean())
        out[f"{name}_pm"] = float(0.5 * (tail.max() - tail.min()))
        out[f"{name}_last"] = float(c[key][-1])
    if out.get("cd"):
        out["l_over_d"] = out["cl"] / out["cd"]
    if "cl" in out:
        out["cl_oscillation"] = out["cl_pm"] * 2 / max(abs(out["cl"]), 0.05)
    out["window"] = int(w)
    return out


def read_forces(case: Path, name: str = "forces1") -> dict[str, np.ndarray] | None:
    """The ``forces`` function object's force.dat (total, pressure and viscous parts, N),
    concatenated across restarts like :func:`read_force_coeffs`."""
    base = Path(case) / "postProcessing" / name
    if not base.is_dir():
        return None
    parts = []
    for d in sorted(base.iterdir(), key=lambda p: float(p.name) if _isnum(p.name) else -1):
        data = _read_dat(d / "force.dat")
        if data and "pressure_x" in data and "viscous_x" in data:
            parts.append(data)
    if not parts:
        return None
    keys = [k for k in parts[0] if all(k in p for p in parts)]
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def force_split(forces: dict[str, np.ndarray], drag_dir, lift_dir, rho: float, airspeed: float,
                area: float, window: int = 200) -> dict:
    """Pressure and viscous parts of CD and CL (means over the trailing ``window``)."""
    q = 0.5 * rho * airspeed**2 * area
    out = {}
    w = min(window, len(forces["total_x"]))
    for part in ("pressure", "viscous"):
        f = np.column_stack([forces[f"{part}_{c}"][-w:] for c in "xyz"])
        out[f"cd_{part}"] = float((f @ np.asarray(drag_dir, dtype=float)).mean() / q)
        out[f"cl_{part}"] = float((f @ np.asarray(lift_dir, dtype=float)).mean() / q)
    return out


def read_y_plus(case: Path) -> dict:
    d = _latest_dir(Path(case) / "postProcessing" / "yPlus1")
    if d is None:
        return {}
    data = _read_dat(d / "yPlus.dat")
    if not data:
        return {}
    out = {}
    patches = data["patch"]
    for p in sorted(set(patches.tolist())):
        sel = np.where(patches == p)[0][-1]
        out[p] = {k: float(data[k][sel]) for k in ("min", "max", "average")}
    return out


def final_residuals(case: Path) -> dict:
    d = _latest_dir(Path(case) / "postProcessing" / "solverInfo1")
    if d is None:
        return {}
    data = _read_dat(d / "solverInfo.dat")
    if not data:
        return {}
    return {k.removesuffix("_initial"): float(v[-1]) for k, v in data.items() if k.endswith("_initial")}


def parse_check_mesh(log: Path) -> dict:
    text = Path(log).read_text(errors="replace") if Path(log).is_file() else ""
    out: dict = {}
    m = re.search(r"^\s*cells:\s+(\d+)", text, re.M)
    if m:
        out["cells"] = int(m.group(1))
    m = re.search(r"non-orthogonality Max: ([\d.eE+-]+)", text)
    if m:
        out["max_non_orthogonality"] = float(m.group(1))
    m = re.search(r"Max skewness = ([\d.eE+-]+)", text)
    if m:
        out["max_skewness"] = float(m.group(1))
    m = re.search(r"Failed (\d+) mesh checks", text)
    out["failed_checks"] = int(m.group(1)) if m else 0
    out["mesh_ok"] = "Mesh OK." in text
    return out


def parse_snappy_layers(log: Path) -> dict:
    """Layer coverage from the final 'patch faces layers overall thickness' table."""
    text = Path(log).read_text(errors="replace") if Path(log).is_file() else ""
    # Last table of the form:
    #   patch faces    layers        overall thickness
    #                  target   mesh     [m]       [%]
    #   ----- -----    -----    ----     ---       ---
    #   body  12328    5        4.67     0.00889   95
    blocks = text.split("overall thickness")
    if len(blocks) < 2:
        return {}
    out: dict = {}
    for line in blocks[-1].splitlines()[3:]:
        f = line.split()
        if len(f) != 6:
            break
        out.setdefault("layers", {})[f[0]] = {
            "faces": int(f[1]),
            "target_layers": int(f[2]),
            "mean_layers": float(f[3]),
            "thickness_m": float(f[4]),
            "thickness_pct": float(f[5]),
        }
    return out
