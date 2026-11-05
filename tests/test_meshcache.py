"""Mesh and solution cache (windlab.meshcache) and its use by the runner and the run manager."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from windlab import meshcache, runner
from windlab.app import Settings, create_app
from windlab.case_builder import FlowConditions, GeometryInput, ReferenceValues, build_case, build_sweep_case
from windlab.geometry.naca import naca_wing
from windlab.meshcache import MeshCache, mesh_key

N = 2
REF = ReferenceValues(area=0.18, length=0.3)


@pytest.fixture
def geom(tmp_path):
    w = naca_wing("0012", chord=0.3, span=0.6)
    p = tmp_path / "w.stl"
    w.write_stl(p)
    return GeometryInput(stl_path=p, bounds_min=tuple(w.vertices.min(0)), bounds_max=tuple(w.vertices.max(0)))


def fake_mesh(case: Path, n: int = N, tag: bytes = b"mesh") -> None:
    """What snappyHexMesh leaves behind: processor*/constant/polyMesh (or constant/polyMesh)."""
    for r in meshcache.rank_dirs(case, n):
        pm = r / "constant" / "polyMesh"
        pm.mkdir(parents=True, exist_ok=True)
        for f in ("points", "faces", "owner", "neighbour", "boundary"):
            (pm / f).write_bytes(tag + f.encode() + b"\0" * 2000)
    (case / "log.snappyHexMesh").write_text("Finished meshing in = 11 s.\n")
    (case / "log.checkMesh").write_text("Mesh OK.\n")


def fake_solution(case: Path, n: int = N, t: int = 240, tag: bytes = b"sol") -> None:
    for r in meshcache.rank_dirs(case, n):
        d = r / str(t)
        d.mkdir(parents=True, exist_ok=True)
        for f in meshcache.FIELD_FILES + ("yPlus",):
            (d / f).write_bytes(tag + f.encode() + b"\0" * 500)


def build(geom, tmp_path, alpha=10.0, airspeed=20.0, name="c", n=N):
    return build_case(geom, FlowConditions(airspeed=airspeed, alpha_deg=alpha), REF, n_procs=n,
                      runs_root=tmp_path / name).path


# --------------------------------------------------------------------------- key


def test_key_ignores_angle_and_speed_but_not_shape_preset_or_ranks(geom, tmp_path):
    a = build(geom, tmp_path, 10, 20, "a")
    b = build(geom, tmp_path, -4, 35, "b")
    assert mesh_key(a) == mesh_key(b)
    assert mesh_key(a) != mesh_key(build(geom, tmp_path, 10, 20, "c", n=3))
    std = build_case(geom, FlowConditions(airspeed=20, alpha_deg=10), REF, preset="standard", n_procs=N,
                     runs_root=tmp_path / "std").path
    assert mesh_key(a) != mesh_key(std)
    other = build_case(geom, FlowConditions(airspeed=20, alpha_deg=10), ReferenceValues(area=0.18, length=0.25),
                       n_procs=N, runs_root=tmp_path / "ref").path  # the reference length sizes the tunnel
    assert mesh_key(a) != mesh_key(other)
    (a / "constant" / "triSurface" / "body.stl").write_bytes(b"turned")  # another orientation = another STL
    assert mesh_key(a) != mesh_key(b)
    assert len(mesh_key(b)) == 32


# --------------------------------------------------------------------------- mesh store / install


def test_store_and_install_mesh(geom, tmp_path):
    cache = MeshCache(tmp_path / "cache")
    a = build(geom, tmp_path, name="a")
    key = mesh_key(a)
    assert cache.lookup(key) is None
    fake_mesh(a)
    report = {"cells": 371_000, "mesh_ok": True, "failed_checks": 0, "layers": {"body": {"thickness_pct": 96.0}}}
    assert cache.store_mesh(key, a, N, report)
    assert not cache.store_mesh(key, a, N, report)  # already there
    meta = cache.lookup(key)
    assert meta["mesh"] == report and meta["n_procs"] == N and meta["bytes"] > 10_000
    b = build(geom, tmp_path, alpha=4, name="b")
    got = cache.install_mesh(key, b, N)
    assert got == report
    for i in range(N):
        pm = b / f"processor{i}" / "constant" / "polyMesh"
        assert (pm / "points").read_bytes() == (a / f"processor{i}" / "constant" / "polyMesh" / "points").read_bytes()
    assert (b / "log.snappyHexMesh").is_file()
    with pytest.raises(KeyError):  # a different rank count can't use this mesh
        cache.install_mesh(key, build(geom, tmp_path, name="c", n=3), 3)
    with pytest.raises(ValueError):
        cache.entry("../etc")
    assert cache.summary()["meshes"] == 1


def test_store_mesh_refuses_an_unmeshed_case(geom, tmp_path):
    cache = MeshCache(tmp_path / "cache")
    a = build(geom, tmp_path)
    assert not cache.store_mesh(mesh_key(a), a, N, {"cells": 1})
    assert cache.lookup(mesh_key(a)) is None


# --------------------------------------------------------------------------- solutions


def test_solutions_nearest_angle_same_speed(geom, tmp_path):
    cache = MeshCache(tmp_path / "cache", max_solutions=3)
    a = build(geom, tmp_path, name="a")
    key = mesh_key(a)
    fake_mesh(a)
    cache.store_mesh(key, a, N, {"cells": 1})
    assert cache.store_solution(key, a, N, {"alpha_deg": 0, "airspeed": 20}) is None  # nothing solved yet
    for alpha, speed, tag in ((0, 20, b"s0"), (8, 20, b"s8"), (12, 20, b"s12"), (4, 40, b"fast")):
        fake_solution(a, tag=tag)
        time.sleep(0.01)
        assert cache.store_solution(key, a, N, {"alpha_deg": alpha, "airspeed": speed, "cl": 0.1}) is not None
    assert len(cache.solutions(key)) == 3  # the oldest (0 deg) was dropped
    sol, meta = cache.nearest_solution(key, 9, 20)
    assert meta["alpha_deg"] == 8
    assert cache.nearest_solution(key, 11, 20)[1]["alpha_deg"] == 12
    assert cache.nearest_solution(key, 10, 20)[1]["alpha_deg"] == 12  # a tie: the newest
    assert cache.nearest_solution(key, 10, 30) is None  # no solution at that airspeed
    assert cache.nearest_solution(key, 4, 40)[1]["alpha_deg"] == 4
    assert cache.nearest_solution("0" * 32, 4, 20) is None
    b = build(geom, tmp_path, alpha=10, name="b")
    with pytest.raises(KeyError):  # start-time folders don't exist before meshing
        cache.install_solution(sol, b, N)
    fake_mesh(b)
    runner._copy_initial_fields(b, N)
    cache.install_solution(sol, b, N)
    for i in range(N):
        assert (b / f"processor{i}" / "0" / "U").read_bytes().startswith(b"s8U")
        assert (b / f"processor{i}" / "0" / "phi").is_file()
    with pytest.raises(ValueError):
        cache.install_solution(tmp_path / "elsewhere", b, N)


# --------------------------------------------------------------------------- housekeeping


def _entry(cache: MeshCache, tmp_path, geom, name, last_used):
    case = build(geom, tmp_path, name=name)
    (case / "constant" / "triSurface" / "body.stl").write_bytes(name.encode() * 10)
    fake_mesh(case)
    key = mesh_key(case)
    cache.store_mesh(key, case, N, {"cells": 1})
    meta = json.loads((cache.entry(key) / "meta.json").read_text())
    meta["last_used"] = last_used
    (cache.entry(key) / "meta.json").write_text(json.dumps(meta))
    return key


def test_housekeeping_keeps_the_recently_used_meshes(geom, tmp_path):
    cache = MeshCache(tmp_path / "cache", max_meshes=2)
    old = _entry(cache, tmp_path, geom, "old", 100)
    mid = _entry(cache, tmp_path, geom, "mid", 200)
    new = _entry(cache, tmp_path, geom, "new", 300)
    assert cache.housekeep() == [old]
    assert cache.lookup(old) is None and cache.lookup(mid) and cache.lookup(new)
    cache.cap_bytes = 10
    assert set(cache.housekeep()) == {mid, new}
    # a half-written entry older than an hour goes; a fresh one is left alone
    stale = cache.root / ("a" * 32)
    stale.mkdir()
    os.utime(stale, (time.time() - 7200,) * 2)
    fresh = cache.root / ("b" * 32)
    fresh.mkdir()
    link = cache.root / ("c" * 32)
    link.symlink_to(tmp_path)
    assert cache.housekeep() == ["a" * 32]
    assert fresh.is_dir() and link.is_symlink() and tmp_path.is_dir()


# --------------------------------------------------------------------------- runner glue


def test_warm_from_latest_builds_the_start_from_the_previous_solution(geom, tmp_path):
    case = build(geom, tmp_path)
    fake_mesh(case)
    assert not runner._warm_from_latest(case, N)  # nothing solved yet
    runner._copy_initial_fields(case, N)
    fake_solution(case, t=120, tag=b"early")
    fake_solution(case, t=240, tag=b"late")
    assert runner._warm_from_latest(case, N)
    for i in range(N):
        d = case / f"processor{i}"
        assert sorted(p.name for p in d.iterdir() if p.is_dir()) == ["0", "constant"]
        assert (d / "0" / "U").read_bytes().startswith(b"lateU")
        assert (d / "0" / "nut").read_bytes().startswith(b"latenut")
        assert not (d / "0" / "yPlus").exists()  # only the solver's own fields come along


def test_prepare_point_warm_and_cold(geom, tmp_path):
    case = build_sweep_case(geom, 20.0, [0, 8], REF, n_procs=N, runs_root=tmp_path / "s").path
    fake_mesh(case)
    runner._copy_initial_fields(case, N)
    assert not runner._prepare_point(case, N, 0, warm=True)  # nothing to warm from: cold start
    fake_solution(case, t=200, tag=b"p0")
    assert runner._prepare_point(case, N, 1, warm=True)
    assert "8" in (case / "system" / "changeDictionaryDict").read_text()
    assert (case / "processor0" / "0" / "p").read_bytes().startswith(b"p0p")
    assert not (case / "processor0" / "200").exists()
    fake_solution(case, t=150, tag=b"p1")
    assert not runner._prepare_point(case, N, 0, warm=False)
    assert not (case / "processor0" / "150").exists()
    assert (case / "processor0" / "0" / "U").read_text().startswith("/*")  # fresh 0.orig fields


class CacheAwareSolver(FakeSolver):
    """The fake solver with the real runner's ``cache`` argument, recording what it got."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.caches = []

    def __call__(self, case_dir, progress=None, cancel=None, on_point=None, cache=None):
        self.caches.append(cache)
        progress("mesh_reused", {"cells": 371_000})
        progress("warm_start", {"alpha_deg": 8.0, "airspeed": 20.0, "iterations": 200})
        return super().__call__(case_dir, progress, cancel, on_point)


def test_run_manager_hands_the_cache_to_a_runner_that_takes_it(tmp_path):
    solver = CacheAwareSolver()
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 10}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(10)
    assert isinstance(solver.caches[0], MeshCache) and solver.caches[0].root == tmp_path / "d" / "mesh-cache"
    s = c.get(f"/api/runs/{rid}").json()
    assert s["params"]["mesh_reused"] is True and s["params"]["warm_start"] == {"alpha_deg": 8.0, "airspeed": 20.0}
    assert "meshes_removed" in a.fastapi.state.runs.housekeep()
    a.fastapi.state.runs.stop()
    # the plain fake (no cache argument) still works: nothing is passed
    b = create_app(Settings(data_dir=tmp_path / "e", runner=FakeSolver()))
    assert b.fastapi.state.runs.cache is not None and not b.fastapi.state.runs._runner_takes_cache
    b.fastapi.state.runs.stop()
