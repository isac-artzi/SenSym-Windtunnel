"""Mesh and solution cache: a later run on the same shape skips the meshing and starts
from the nearest earlier result (task 010, steps 3a and 3b).

Layout under ``<data>/mesh-cache/``::

    <key>/meta.json                     key, dates, n_procs, the mesh report (cells, quality)
    <key>/mesh/processor<i>/constant/polyMesh/...   (or mesh/constant/polyMesh for one rank)
    <key>/logs/log.snappyHexMesh, log.checkMesh
    <key>/solutions/<uuid>/meta.json    alpha_deg, airspeed, iterations, CL, CD, dates
    <key>/solutions/<uuid>/processor<i>/{U,p,k,omega,nut,phi}   (or at the top for one rank)

The key is a hash of everything that shapes the mesh: the STL bytes (so an upload turned
by 90 degrees gets a new mesh), the rendered mesh dictionaries (blockMeshDict,
snappyHexMeshDict, surfaceFeatureExtractDict, decomposeParDict - so the preset, the
reference length, the rank count and any template change all invalidate it) and a format
version. The angle of attack and the airspeed are not in the key: they only enter the
freestream (0.orig/U, controlDict), never the mesh (the boundary-layer settings are
relative to the surface cell, not to the speed).

Housekeeping keeps the most recently used few meshes and a few solutions per mesh, under
a byte cap. Only hex-named folders directly inside the cache root are ever touched and
symlinks are never followed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

log = logging.getLogger("windlab.meshcache")

FORMAT_VERSION = "1"
MESH_INPUTS = ("constant/triSurface/body.stl", "constant/triSurface/thin.stl", "system/blockMeshDict",
               "system/snappyHexMeshDict", "system/surfaceFeatureExtractDict", "system/decomposeParDict")
FIELD_FILES = ("U", "p", "k", "omega", "nut", "phi")
LOG_FILES = ("log.blockMesh", "log.surfaceFeatureExtract", "log.decomposePar", "log.snappyHexMesh", "log.checkMesh")
DEFAULT_MAX_MESHES = 3
DEFAULT_MAX_SOLUTIONS = 4
DEFAULT_CAP_BYTES = 2 * 2**30
SAME_SPEED_TOL = 0.01  # a solution counts as "the same airspeed" within 1 %

_KEY = re.compile(r"^[0-9a-f]{32}$")


def mesh_key(case: Path) -> str:
    """Hash of the files that decide the mesh (see the module docstring)."""
    h = hashlib.sha256(FORMAT_VERSION.encode())
    for rel in MESH_INPUTS:
        p = Path(case) / rel
        h.update(rel.encode() + b"\0")
        h.update(p.read_bytes() if p.is_file() and not p.is_symlink() else b"-")
        h.update(b"\0")
    return h.hexdigest()[:32]


def _is_time(name: str) -> bool:
    try:
        float(name)
        return True
    except ValueError:
        return False


def latest_time_dir(where: Path) -> Path | None:
    """The time folder with the largest value (solution written last), or None."""
    best = None
    if not where.is_dir():
        return None
    for d in where.iterdir():
        if d.is_dir() and not d.is_symlink() and _is_time(d.name):
            t = float(d.name)
            if best is None or t > best[0]:
                best = (t, d)
    return best[1] if best else None


def rank_dirs(case: Path, n: int) -> list[Path]:
    """Where the mesh and fields of each rank live: processor0..N-1, or the case itself."""
    return [Path(case) / f"processor{i}" for i in range(n)] if n > 1 else [Path(case)]


def _copytree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)


def tree_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    return total


def _read_json(p: Path) -> dict | None:
    if not p.is_file() or p.is_symlink():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _write_json(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, p)


class MeshCache:
    def __init__(self, root: Path, max_meshes: int = DEFAULT_MAX_MESHES, max_solutions: int = DEFAULT_MAX_SOLUTIONS,
                 cap_bytes: int = DEFAULT_CAP_BYTES):
        self.root = Path(root)
        self.max_meshes = max(1, int(max_meshes))
        self.max_solutions = max(0, int(max_solutions))
        self.cap_bytes = int(cap_bytes)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ entries

    def entry(self, key: str) -> Path:
        if not _KEY.match(key or ""):
            raise ValueError("bad mesh cache key")
        return self.root / key

    def entries(self) -> list[tuple[Path, dict]]:
        out = []
        for d in self.root.iterdir():
            if not _KEY.match(d.name) or not d.is_dir() or d.is_symlink():
                continue
            meta = _read_json(d / "meta.json")
            if meta is not None:
                out.append((d, meta))
        return out

    def lookup(self, key: str) -> dict | None:
        """The stored mesh report for ``key`` (and a touch of its last-used time), or None."""
        d = self.entry(key)
        meta = _read_json(d / "meta.json")
        if meta is None or not (d / "mesh").is_dir():
            return None
        meta["last_used"] = time.time()
        with contextlib.suppress(OSError):
            _write_json(d / "meta.json", meta)
        return meta

    # ------------------------------------------------------------------ mesh

    def store_mesh(self, key: str, case: Path, n: int, mesh_report: dict) -> bool:
        """Copy the meshed case's polyMesh folders (and meshing logs) into the cache.
        Returns False if the entry already exists or the mesh is not where it should be."""
        d = self.entry(key)
        if (d / "meta.json").is_file():
            return False
        srcs = [(r, r / "constant" / "polyMesh") for r in rank_dirs(case, n)]
        if not all(pm.is_dir() and not pm.is_symlink() for _, pm in srcs):
            return False
        tmp = self.root / f".tmp-{uuid.uuid4().hex}"
        try:
            for r, pm in srcs:
                rel = r.relative_to(case) if r != Path(case) else Path()
                _copytree(pm, tmp / "mesh" / rel / "constant" / "polyMesh")
            (tmp / "logs").mkdir()
            for name in LOG_FILES:
                f = Path(case) / name
                if f.is_file() and not f.is_symlink():
                    shutil.copyfile(f, tmp / "logs" / name)
            (tmp / "solutions").mkdir()
            meta = {"key": key, "format": FORMAT_VERSION, "created": time.time(), "last_used": time.time(),
                    "n_procs": int(n), "mesh": dict(mesh_report)}
            _write_json(tmp / "meta.json", meta)
            meta["bytes"] = tree_size(tmp)
            _write_json(tmp / "meta.json", meta)
            os.rename(tmp, d)  # fails if another store of the same key got there first
        except OSError as e:
            log.warning("mesh cache: could not store %s: %s", key, e)
            shutil.rmtree(tmp, ignore_errors=True)
            return False
        return True

    def install_mesh(self, key: str, case: Path, n: int) -> dict:
        """Put the cached mesh (and its logs) into a freshly built case. Returns the mesh report."""
        d = self.entry(key)
        meta = _read_json(d / "meta.json")
        if meta is None or int(meta.get("n_procs", -1)) != int(n):
            raise KeyError(key)
        for r in rank_dirs(case, n):
            rel = r.relative_to(case) if r != Path(case) else Path()
            src = d / "mesh" / rel / "constant" / "polyMesh"
            if not src.is_dir() or src.is_symlink():
                raise KeyError(key)
            dst = r / "constant" / "polyMesh"
            shutil.rmtree(dst, ignore_errors=True)
            _copytree(src, dst)
        for name in LOG_FILES:
            f = d / "logs" / name
            if f.is_file() and not f.is_symlink():
                shutil.copyfile(f, Path(case) / name)
        return dict(meta.get("mesh") or {})

    # ------------------------------------------------------------------ solutions

    def store_solution(self, key: str, case: Path, n: int, info: dict) -> Path | None:
        """Keep the latest written fields of ``case`` as a warm start for later runs.
        ``info`` carries alpha_deg, airspeed and the result numbers (no user text)."""
        d = self.entry(key)
        if not (d / "meta.json").is_file() or self.max_solutions == 0:
            return None
        ranks = rank_dirs(case, n)
        times = [latest_time_dir(r) for r in ranks]
        if any(t is None or float(t.name) <= 0 for t in times):
            return None  # nothing solved
        sols = d / "solutions"
        sols.mkdir(exist_ok=True)
        tmp = sols / f".tmp-{uuid.uuid4().hex}"
        try:
            for r, t in zip(ranks, times, strict=True):
                rel = r.relative_to(case) if r != Path(case) else Path()
                dst = tmp / rel
                dst.mkdir(parents=True, exist_ok=True)
                for f in FIELD_FILES:
                    src = t / f
                    if not src.is_file() or src.is_symlink():
                        raise OSError(f"missing field {f} in {t}")
                    shutil.copyfile(src, dst / f)
            meta = {k: info.get(k) for k in ("alpha_deg", "airspeed", "iterations", "converged", "cl", "cd")}
            meta.update({"created": time.time(), "last_used": time.time(), "n_procs": int(n),
                         "from_time": float(times[0].name)})
            _write_json(tmp / "meta.json", meta)
            meta["bytes"] = tree_size(tmp)
            _write_json(tmp / "meta.json", meta)
            final = sols / uuid.uuid4().hex
            os.rename(tmp, final)
        except OSError as e:
            log.warning("mesh cache: could not store a solution for %s: %s", key, e)
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        self._prune_solutions(d)
        return final

    def solutions(self, key: str) -> list[tuple[Path, dict]]:
        d = self.entry(key) / "solutions"
        out = []
        if not d.is_dir():
            return out
        for s in d.iterdir():
            if s.is_dir() and not s.is_symlink() and re.fullmatch(r"[0-9a-f]{32}", s.name):
                meta = _read_json(s / "meta.json")
                if meta is not None:
                    out.append((s, meta))
        return out

    def nearest_solution(self, key: str, alpha_deg: float, airspeed: float) -> tuple[Path, dict] | None:
        """The stored solution at the same airspeed (within 1 %) and the nearest angle, newest
        first on ties. None when there is none: a different airspeed means different k, omega
        and Reynolds number, so the run starts from scratch instead."""
        try:
            cands = self.solutions(key)
        except ValueError:
            return None
        same = [(s, m) for s, m in cands
                if m.get("airspeed") and abs(float(m["airspeed"]) - airspeed) <= SAME_SPEED_TOL * airspeed
                and m.get("alpha_deg") is not None]
        if not same:
            return None
        best = min(same, key=lambda sm: (abs(float(sm[1]["alpha_deg"]) - alpha_deg), -float(sm[1].get("created", 0))))
        best[1]["last_used"] = time.time()
        with contextlib.suppress(OSError):
            _write_json(best[0] / "meta.json", best[1])
        return best

    def install_solution(self, solution: Path, case: Path, n: int) -> None:
        """Copy a stored solution's fields over the case's start-time fields (processor*/0 or 0)."""
        solution = Path(solution).resolve()
        if solution.parent.parent.parent != self.root.resolve() or solution.parent.name != "solutions":
            raise ValueError("solution is not inside the cache")
        for r in rank_dirs(case, n):
            rel = r.relative_to(case) if r != Path(case) else Path()
            src = solution / rel
            dst = r / "0"
            if not dst.is_dir():
                raise KeyError("the case has no start-time folder yet")
            for f in FIELD_FILES:
                s = src / f
                if not s.is_file() or s.is_symlink():
                    raise KeyError(f"stored solution lacks {f}")
                tmp = dst / (f + ".tmp")
                shutil.copyfile(s, tmp)
                os.replace(tmp, dst / f)

    def _prune_solutions(self, entry: Path) -> None:
        sols = sorted(self.solutions(entry.name), key=lambda sm: float(sm[1].get("last_used") or 0))
        while len(sols) > self.max_solutions:
            s, _ = sols.pop(0)
            shutil.rmtree(s, ignore_errors=True)

    # ------------------------------------------------------------------ housekeeping

    def housekeep(self) -> list[str]:
        """Drop the least recently used meshes beyond ``max_meshes`` or the byte cap, and
        half-written entries older than an hour. Returns the removed keys."""
        removed = []
        now = time.time()
        for d in self.root.iterdir():
            if d.is_symlink() or not d.is_dir():
                continue
            if d.name.startswith(".tmp-") and now - d.stat().st_mtime > 3600:
                shutil.rmtree(d, ignore_errors=True)
            elif _KEY.match(d.name) and not (d / "meta.json").is_file() and now - d.stat().st_mtime > 3600:
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        entries = sorted(self.entries(), key=lambda dm: float(dm[1].get("last_used") or 0))
        sizes = {d: tree_size(d) for d, _ in entries}
        total = sum(sizes.values())
        while entries and (len(entries) > self.max_meshes or total > self.cap_bytes):
            d, _ = entries.pop(0)
            shutil.rmtree(d, ignore_errors=True)
            total -= sizes[d]
            removed.append(d.name)
        if removed:
            log.info("mesh cache: removed %d entr%s", len(removed), "y" if len(removed) == 1 else "ies")
        return removed

    def summary(self) -> dict:
        entries = self.entries()
        return {"meshes": len(entries), "solutions": sum(len(self.solutions(d.name)) for d, _ in entries),
                "bytes": sum(tree_size(d) for d, _ in entries)}
