"""Disk housekeeping for the data folder (runs + geometry).

* :func:`slim_case` - once a run's results are extracted, delete the bulky solver files
  (``processor*`` folders, time folders, the mesh, raw surface/slice samples). What is
  left - status, report, logs, force-coefficient history and the pictures WindLab made -
  is a few MB.
* :func:`prune_runs` - keep at most ``keep`` finished runs and the whole data folder under
  ``cap_bytes``, deleting the oldest finished runs first. Queued and running runs are
  never touched.
* :func:`prune_geometry` - shapes that no remaining run uses and that were last used more
  than ``max_age_days`` ago are removed; so are stale temporary uploads.

Every path is checked to be a real (non-symlink) folder directly inside its root.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
from collections.abc import Iterable
from pathlib import Path

from windlab.app.geometry_store import GeometryError, parse_uuid

log = logging.getLogger("windlab.housekeeping")

DEFAULT_KEEP_RUNS = 20
DEFAULT_DISK_CAP_BYTES = 10 * 2**30
DEFAULT_GEOMETRY_MAX_AGE_DAYS = 7.0
FINISHED = frozenset({"done", "failed", "cancelled"})

# Kept inside a slimmed case folder (relative paths); everything else is removed.
_KEEP_FILES = {"windlab_case.json", "windlab_status.json", "windlab_report.json"}
_KEEP_PREFIXES = ("windlab_", "log.")
_KEEP_POSTPROCESSING = {"forceCoeffs1", "yPlus1", "solverInfo1"}


def tree_size(path: Path) -> int:
    """Bytes used by the files under ``path`` (symlinks not followed)."""
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    return total


def _child_dirs(root: Path) -> list[Path]:
    """UUID-named, real folders directly inside ``root``."""
    out = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        try:
            parse_uuid(d.name)
        except GeometryError:
            continue
        if d.is_dir() and not d.is_symlink():
            out.append(d)
    return out


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _slim_postprocessing(pp: Path) -> None:
    if not pp.is_dir() or pp.is_symlink():
        return
    for d in pp.iterdir():
        if d.name not in _KEEP_POSTPROCESSING:
            _remove(d)


def slim_case(case: Path) -> int:
    """Delete bulky solver files from a finished case folder; returns the bytes freed."""
    case = Path(case)
    if not case.is_dir() or case.is_symlink():
        return 0
    before = tree_size(case)
    for item in case.iterdir():
        name = item.name
        if name in _KEEP_FILES or name.startswith(_KEEP_PREFIXES):
            continue
        if name == "postProcessing":
            _slim_postprocessing(item)
        elif name == "sweep" and item.is_dir() and not item.is_symlink():
            for p in item.iterdir():  # per-angle inputs are tiny; keep their force history
                if p.is_dir() and not p.is_symlink():
                    _slim_postprocessing(p / "postProcessing")
        elif name in ("system", "0.orig"):
            continue  # small text dictionaries: show exactly how the case was set up
        elif name == "constant":
            for sub in ("polyMesh", "extendedFeatureEdgeMesh"):
                _remove(item / sub)
            for f in (item / "triSurface").glob("*.eMesh"):
                _remove(f)
        else:  # processor*, time folders, anything else the solver wrote
            _remove(item)
    return max(0, before - tree_size(case))


def _status(case: Path) -> dict | None:
    f = case / "windlab_status.json"
    if not f.is_file() or f.is_symlink():
        return None
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def prune_runs(runs_root: Path, data_root: Path, active: Iterable[str] = (), keep: int = DEFAULT_KEEP_RUNS,
               cap_bytes: int = DEFAULT_DISK_CAP_BYTES) -> list[str]:
    """Delete the oldest finished runs until at most ``keep`` remain and the data folder
    fits in ``cap_bytes``. Runs in ``active`` (queued / running) are never deleted.
    Returns the deleted run ids."""
    active = set(active)
    finished = []
    for d in _child_dirs(Path(runs_root)):
        if d.name in active:
            continue
        st = _status(d)
        # A folder without a status file is a half-written case (left alone while it may
        # still be being written); a stale "running" status on disk means WindLab was
        # closed mid-run. Both count as finished.
        if st is None and time.time() - d.stat().st_mtime < 3600:
            continue
        when = (st or {}).get("finished") or (st or {}).get("created") or d.stat().st_mtime
        finished.append((float(when), d))
    finished.sort(key=lambda t: t[0])
    deleted: list[str] = []
    total = tree_size(Path(data_root))
    sizes = {d: tree_size(d) for _, d in finished}
    while finished and (len(finished) > keep or total > cap_bytes):
        _, d = finished.pop(0)
        _remove(d)
        total -= sizes[d]
        deleted.append(d.name)
    if deleted:
        log.info("housekeeping: removed %d old run(s)", len(deleted))
    return deleted


def prune_geometry(geometry_root: Path, runs_root: Path, active_geometry: Iterable[str] = (),
                   max_age_days: float = DEFAULT_GEOMETRY_MAX_AGE_DAYS, now: float | None = None) -> list[str]:
    """Remove shapes no remaining run refers to and not used for ``max_age_days``."""
    now = time.time() if now is None else now
    used = set(active_geometry)
    for d in _child_dirs(Path(runs_root)):
        st = _status(d)
        if st and st.get("geometry_id"):
            used.add(st["geometry_id"])
    deleted = []
    for d in _child_dirs(Path(geometry_root)):
        if d.name in used:
            continue
        meta_f = d / "geometry.json"
        try:
            meta = json.loads(meta_f.read_text()) if meta_f.is_file() and not meta_f.is_symlink() else {}
        except (OSError, ValueError):
            meta = {}
        last = max(float(meta.get("last_used") or 0), float(meta.get("created") or 0)) or d.stat().st_mtime
        if now - last > max_age_days * 86400:
            _remove(d)
            deleted.append(d.name)
    if deleted:
        log.info("housekeeping: removed %d unused shape(s)", len(deleted))
    return deleted


def prune_incoming(incoming: Path, max_age_s: float = 86400, now: float | None = None) -> int:
    """Temporary upload files left behind by a crash."""
    now = time.time() if now is None else now
    n = 0
    if not incoming.is_dir():
        return 0
    for f in incoming.iterdir():
        if f.is_file() and not f.is_symlink() and f.name.startswith("upload-") and now - f.stat().st_mtime > max_age_s:
            f.unlink(missing_ok=True)
            n += 1
    return n
