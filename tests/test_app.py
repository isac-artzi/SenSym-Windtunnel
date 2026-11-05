"""Web app API: geometry, runs (with a fake solver), surface data, security guards."""

from __future__ import annotations

import base64
import json
import struct
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

from tests.asgi_client import make_client
from windlab.app import Settings, create_app
from windlab.app import surface as surf
from windlab.geometry.stl import write_binary_stl
from windlab.runner import RunCancelled, RunError, RunReport

CUBE_V = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
CUBE_F = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                   [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [3, 0, 4], [3, 4, 7]])
CUBE_QUADS = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [3, 0, 4, 7]]


def _b64(arr: np.ndarray) -> str:
    raw = np.ascontiguousarray(arr).tobytes()
    return base64.b64encode(struct.pack("<Q", len(raw)) + raw).decode()


def write_vtp(path: Path, points: np.ndarray, polys: list[list[int]], p: np.ndarray) -> None:
    """A VTK PolyData file in the layout OpenFOAM's surfaces function object writes."""
    conn = np.array([i for poly in polys for i in poly], "<i4")
    offs = np.cumsum([len(poly) for poly in polys]).astype("<i4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""<?xml version='1.0'?>
<VTKFile type='PolyData' version='0.1' byte_order='LittleEndian' header_type='UInt64'>
  <PolyData>
    <Piece NumberOfPoints='{len(points)}' NumberOfPolys='{len(polys)}'>
      <Points>
        <DataArray type='Float32' Name='Points' NumberOfComponents='3' format='binary'>
{_b64(points.astype("<f4"))}
        </DataArray>
      </Points>
      <Polys>
        <DataArray type='Int32' Name='connectivity' format='binary'>
{_b64(conn)}
        </DataArray>
        <DataArray type='Int32' Name='offsets' format='binary'>
{_b64(offs)}
        </DataArray>
      </Polys>
      <CellData>
        <DataArray type='Float32' Name='p' format='binary'>
{_b64(p.astype("<f4"))}
        </DataArray>
      </CellData>
    </Piece>
  </PolyData>
</VTKFile>
""")


def write_slice_vtp(path: Path, U: float) -> None:
    """A midPlane slice like OpenFOAM's (quads on y = 0, point values of U and p)."""
    xs, zs = np.linspace(-0.2, 0.8, 11), np.linspace(-0.2, 0.2, 5)
    X, Z = np.meshgrid(xs, zs)
    pts = np.column_stack([X.ravel(), np.zeros(X.size), Z.ravel()])
    nx = len(xs)
    quads = [[j * nx + i, j * nx + i + 1, (j + 1) * nx + i + 1, (j + 1) * nx + i]
             for j in range(len(zs) - 1) for i in range(nx - 1)]
    speed = 1.0 + 0.3 * np.exp(-((pts[:, 0] - 0.1) ** 2 + pts[:, 2] ** 2) / 0.01)
    vel = np.column_stack([speed * U, np.zeros(len(pts)), np.zeros(len(pts))])
    p = 0.5 * U**2 * (1 - speed**2)
    conn = np.array([i for q in quads for i in q], "<i4")
    offs = np.cumsum([4] * len(quads)).astype("<i4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""<?xml version='1.0'?>
<VTKFile type='PolyData' version='0.1' byte_order='LittleEndian' header_type='UInt64'>
  <PolyData>
    <FieldData>
      <DataArray type='Float32' Name='TimeValue' NumberOfTuples='1' format='binary'>
{_b64(np.array([300.0], "<f4"))}
      </DataArray>
    </FieldData>
    <Piece NumberOfPoints='{len(pts)}' NumberOfPolys='{len(quads)}'>
      <Points>
        <DataArray type='Float32' Name='Points' NumberOfComponents='3' format='binary'>
{_b64(pts.astype("<f4"))}
        </DataArray>
      </Points>
      <Polys>
        <DataArray type='Int32' Name='connectivity' format='binary'>
{_b64(conn)}
        </DataArray>
        <DataArray type='Int32' Name='offsets' format='binary'>
{_b64(offs)}
        </DataArray>
      </Polys>
      <PointData>
        <DataArray type='Float32' Name='p' format='binary'>
{_b64(p.astype("<f4"))}
        </DataArray>
        <DataArray type='Float32' Name='U' NumberOfComponents='3' format='binary'>
{_b64(vel.astype("<f4"))}
        </DataArray>
      </PointData>
    </Piece>
  </PolyData>
</VTKFile>
""")


class FakeSolver:
    """Stands in for windlab.runner.run_case: writes the files the real stages would."""

    def __init__(self, iterations=300, fail_stage=None, delay=0.0, fail_angles=()):
        self.iterations, self.fail_stage, self.delay = iterations, fail_stage, delay
        self.fail_angles = set(fail_angles)
        self.calls = []

    def _wait(self, stage, cancel):
        end = time.monotonic() + self.delay
        while time.monotonic() < end:
            if cancel is not None and cancel.is_set():
                raise RunCancelled(stage)
            time.sleep(0.005)
        if cancel is not None and cancel.is_set():
            raise RunCancelled(stage)

    def __call__(self, case_dir: Path, progress=None, cancel=None, on_point=None):
        case = Path(case_dir)
        self.calls.append(case)
        meta = json.loads((case / "windlab_case.json").read_text())
        rep = RunReport(run_id=meta["run_id"], case_dir=str(case), n_procs=meta["n_procs"])
        for stage in ("blockMesh", "surfaceFeatureExtract", "snappyHexMesh", "checkMesh", "potentialFoam",
                      "simpleFoam"):
            progress("stage", {"stage": stage})
            if stage == self.fail_stage:
                raise RunError(stage, "exited with code 1")
            self._wait(stage, cancel)
        # bulky solver output that housekeeping removes once the results are extracted
        for p in ("processor0/350", "processor1/350", "350"):
            (case / p).mkdir(parents=True)
            (case / p / "U").write_bytes(b"\0" * 1000)
        if meta.get("sweep"):
            return self._sweep(case, meta, rep, progress, cancel, on_point)
        n = self.iterations
        it = np.arange(1, n + 1)
        cl = 0.45 * (1 - np.exp(-it / 50))
        cd = 0.05 + 0.02 * np.exp(-it / 50)
        d = case / "postProcessing" / "forceCoeffs1" / "0"
        d.mkdir(parents=True)
        (d / "coefficient.dat").write_text(
            "# Time Cd Cl CmPitch\n" + "".join(f"{i} {b} {a} 0.01\n" for i, a, b in zip(it, cl, cd, strict=True))
        )
        U = meta["airspeed"]
        write_vtp(case / "postProcessing" / "bodySurface" / str(n) / "body.vtp", CUBE_V * 0.3, CUBE_QUADS,
                  np.linspace(-1.0, 1.0, 6) * 0.5 * U**2)
        write_slice_vtp(case / "postProcessing" / "midPlane" / str(n) / "plane.vtp", U)
        progress("converged", {"iteration": n})
        rep.iterations, rep.converged = n, True
        rep.mesh = {"cells": 371_000, "mesh_ok": True, "failed_checks": 0,
                    "layers": {"body": {"thickness_pct": 95.0}}}
        rep.coefficients = {"cl": 0.45, "cd": 0.05, "cm": 0.01, "cl_pm": 0.001, "cd_pm": 0.0001,
                            "l_over_d": 9.0, "cl_oscillation": 0.004}
        return rep

    @staticmethod
    def sweep_cl(a):
        """A wing that stalls past 12 degrees."""
        return 0.075 * a if a <= 12 else 0.9 - 0.05 * (a - 12)

    def _sweep(self, case, meta, rep, progress, cancel, on_point):
        from windlab import runner

        U = meta["airspeed"]
        for i, pt in enumerate(meta["sweep"]["points"]):
            progress("point", {"index": i, "alpha_deg": pt["alpha_deg"], "count": len(meta["sweep"]["points"])})
            time.sleep(self.delay / 2)  # the real runner prepares the angle's files here
            progress("stage", {"stage": "simpleFoam"})
            self._wait("simpleFoam", cancel)
            a = pt["alpha_deg"]
            # each angle's inputs were rendered from the template with its own freestream
            ctrl = (runner.point_dir(case, i) / "controlDict").read_text()
            assert "liftDir" in ctrl
            if a in self.fail_angles:
                res = {"status": "failed", "stage": "simpleFoam", "error": "exited with code 1"}
            else:
                cl, cd = self.sweep_cl(a), 0.02 + 0.002 * a * a
                d = case / "postProcessing" / "forceCoeffs1" / "0"
                d.mkdir(parents=True, exist_ok=True)
                n = 300 + 20 * i
                (d / "coefficient.dat").write_text(
                    "# Time Cd Cl CmPitch\n" + "".join(f"{k} {cd} {cl} -0.01\n" for k in range(1, n + 1)))
                write_vtp(case / "postProcessing" / "bodySurface" / str(n) / "body.vtp", CUBE_V * 0.3, CUBE_QUADS,
                          np.linspace(-1.0, 1.0, 6) * 0.5 * U**2)
                write_slice_vtp(case / "postProcessing" / "midPlane" / str(n) / "plane.vtp", U)
                res = {"status": "done", "iterations": n, "converged": a != 16,
                       "coefficients": {"cl": cl, "cd": cd, "cm": -0.01, "l_over_d": cl / cd, "cl_pm": 0.0,
                                        "cd_pm": 0.0, "cl_oscillation": 0.0}}
            res["alpha_deg"], res["seconds"] = a, 1.0
            on_point(i, res)
            runner._archive_point(case, i)
            rep.points.append(res)
        rep.mesh = {"cells": 371_000, "mesh_ok": True, "failed_checks": 0}
        rep.iterations = sum(p.get("iterations", 0) for p in rep.points)
        return rep


@pytest.fixture
def solver():
    return FakeSolver()


@pytest.fixture
def app(tmp_path, solver):
    a = create_app(Settings(data_dir=tmp_path / "data", n_procs=2, runner=solver))
    yield a
    a.fastapi.state.runs.stop()


@pytest.fixture
def client(app):
    return make_client(app)


def wait_done(client, app, run_id):
    assert app.fastapi.state.runs.wait_idle(20)
    return client.get(f"/api/runs/{run_id}").json()


def naca(client, **kw):
    body = {"code": "0012", "chord": 0.3, "span": 0.6, **kw}
    return client.post("/api/geometry/naca", json=body)


def cube_bytes(tmp_path, scale=300.0, faces=CUBE_F):
    p = tmp_path / f"cube_{uuid.uuid4().hex}.stl"
    write_binary_stl(p, CUBE_V * scale, faces)
    return p.read_bytes()


# --------------------------------------------------------------------------- config


def test_config(client):
    c = client.get("/api/config").json()
    assert c["version"] and isinstance(c["commit"], str) and c["commit"]  # a short sha or "unknown"
    assert {x["name"] for x in c["third_party"]} >= {"three.js", "NumPy"} and c["trademark"]["disclaimer"]
    assert c["airspeed"]["min"] == 1 and c["airspeed"]["max"] == 100
    assert [p["id"] for p in c["presets"]][:2] == ["quick", "standard"] and c["n_procs"] == 2
    assert c["presets"][1]["label"] == "More accurate, slower"
    assert "not yet validated" in c["label"]


# --------------------------------------------------------------------------- geometry


def test_naca_geometry(client):
    r = naca(client)
    assert r.status_code == 200, r.text
    g = r.json()
    uuid.UUID(g["geometry_id"], version=4)
    assert g["usable"] and g["size_m"][0] == pytest.approx(0.3, rel=1e-3)
    assert g["size_m"][1] == pytest.approx(0.6, rel=1e-6)
    assert g["reference"]["area"] == pytest.approx(0.18)
    assert client.get(f"/api/geometry/{g['geometry_id']}").json()["name"].startswith("NACA 0012")
    prev = client.get(f"/api/geometry/{g['geometry_id']}/preview")
    assert prev.status_code == 200 and surf.decode(prev.content)["cp"] is None


@pytest.mark.parametrize("body, word", [
    ({"code": "12"}, "code"),
    ({"code": "00a2"}, "four digits"),
    ({"code": "2012"}, "camber position"),
    ({"chord": 5.0}, "Chord must be between"),
    ({"span": 0.01}, "Span must be between"),
    ({"chord": 0.05, "span": 4.0}, "at most 20 times"),
    ({"taper": 0.1}, "Taper"),
    ({"extra": "#codeStream"}, "extra"),
])
def test_naca_rejects_bad_input(client, body, word):
    r = naca(client, **body)
    assert r.status_code in (400, 422)
    assert word in r.json()["detail"]


def test_upload_mm_cube_is_scaled(client, app, tmp_path):
    r = client.post("/api/geometry", params={"units": "mm", "name": "my cube.stl"}, content=cube_bytes(tmp_path))
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["usable"] and g["longest_m"] == pytest.approx(0.3)
    assert not g["size_suspicious"] and g["name"] == "my cube.stl"
    stl = app.fastapi.state.geometry.stl_path(g["geometry_id"])
    v = np.frombuffer(stl.read_bytes(), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                      offset=84)["v"]
    assert v.max() == pytest.approx(0.3, rel=1e-6)  # stored in metres
    assert g["reference"]["length"] == pytest.approx(0.3)
    assert not list((tmp_path / "data" / "incoming").iterdir())  # temp upload removed


def test_upload_name_is_sanitised(client, tmp_path):
    r = client.post("/api/geometry", params={"units": "mm", "name": "<script>x</script>../../etc"},
                    content=cube_bytes(tmp_path))
    assert "<" not in r.json()["name"] and "/" not in r.json()["name"]


def test_upload_bad_units_and_garbage(client, tmp_path):
    r = client.post("/api/geometry", params={"units": "ft"}, content=cube_bytes(tmp_path))
    assert r.status_code == 422 or "units" in r.json()["detail"]
    r = client.post("/api/geometry", params={"units": "mm"}, content=b"hello, this is not an STL" * 10)
    assert r.status_code == 400 and "not an STL" in r.json()["detail"]
    r = client.post("/api/geometry", params={"units": "mm"}, content=b"")
    assert r.status_code == 400


def test_upload_open_surface_is_reported_not_usable(client, tmp_path):
    r = client.post("/api/geometry", params={"units": "mm"}, content=cube_bytes(tmp_path, faces=CUBE_F[2:]))
    g = r.json()
    assert r.status_code == 200 and not g["usable"] and any("holes" in p for p in g["problems"])
    r = client.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0})
    assert r.status_code == 400 and "problems" in r.json()["detail"]


def test_upload_size_limit(tmp_path, solver):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver, max_upload_bytes=500))
    c = make_client(a)
    r = c.post("/api/geometry", params={"units": "mm"}, content=b"x" * 2000)
    assert r.status_code == 413 and "limit" in r.json()["detail"]


def test_bad_geometry_ids(client):
    assert client.get("/api/geometry/not-a-uuid").status_code == 400
    assert client.get("/api/geometry/..%2F..%2Fetc").status_code in (400, 404)
    assert client.get(f"/api/geometry/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/api/geometry/{uuid.uuid1()}").status_code == 400  # not a v4 id


# --------------------------------------------------------------------------- runs


def test_run_lifecycle_with_fake_solver(client, app, solver):
    gid = naca(client).json()["geometry_id"]
    r = client.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 10, "preset": "quick"})
    assert r.status_code == 202, r.text
    run = r.json()
    uuid.UUID(run["run_id"], version=4)
    assert run["status"] in ("queued", "running", "done")
    assert run["params"]["reynolds"] == pytest.approx(20 * 0.3 / 1.4607e-5, rel=1e-3)
    s = wait_done(client, app, run["run_id"])
    assert s["status"] == "done", s
    assert s["percent"] == 100 and s["stage"] == "done"
    assert s["result"]["cl"] == pytest.approx(0.45) and s["result"]["l_over_d"] == pytest.approx(9.0)
    assert "not yet validated" in s["result"]["label"] and "not yet validated" in s["label"]
    assert s["history"][-1][0] == 300 and len(s["history"]) <= 401
    assert s["surface_available"] and s["warnings"] == []
    assert set(s["stage_times"]) >= {"snappyHexMesh", "simpleFoam"}
    # case folder came from the template, under the runs folder, with no user text in it
    case = solver.calls[0]
    assert case.parent == app.fastapi.state.runs.root and (case / "system" / "controlDict").is_file()


def test_surface_binary_and_json(client, app):
    gid = naca(client).json()["geometry_id"]
    rid = client.post("/api/runs", json={"geometry_id": gid, "airspeed": 10, "alpha_deg": 0}).json()["run_id"]
    wait_done(client, app, rid)
    b = client.get(f"/api/runs/{rid}/surface")
    assert b.status_code == 200 and b.headers["content-type"] == "application/octet-stream"
    d = surf.decode(b.content)
    assert d["vertices"].shape == (8, 3) and d["triangles"].shape == (12, 3)
    # face Cp runs -1 ... 1; per-vertex area averages stay inside that and keep the sign spread
    assert -1 <= d["cp_min"] < 0 < d["cp_max"] <= 1
    assert d["cp"].min() == pytest.approx(d["cp_min"]) and d["vertices"].max() == pytest.approx(0.3)
    j = client.get(f"/api/runs/{rid}/surface", params={"format": "json"}).json()
    assert len(j["cp"]) == 8 and len(j["triangles"]) == 12
    assert client.get(f"/api/runs/{rid}/surface", params={"format": "xml"}).status_code == 422


def test_runs_execute_one_at_a_time_in_order(tmp_path):
    solver = FakeSolver(delay=0.02)
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    t0 = time.monotonic()
    ids = [c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": al}).json()["run_id"]
           for al in (0, 5, 10)]
    assert time.monotonic() - t0 < 2  # requests do not wait for runs
    assert a.fastapi.state.runs.wait_idle(20)
    assert [p.name for p in solver.calls] == ids
    st = [a.fastapi.state.runs.get(i) for i in ids]
    for earlier, later in zip(st, st[1:], strict=False):
        assert earlier.finished <= later.started
    a.fastapi.state.runs.stop()


def test_queue_position_reported(tmp_path):
    solver = FakeSolver(delay=0.3)
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    first = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()
    second = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 5}).json()
    assert second["status"] == "queued" and second["queue_position"] >= 1
    assert "Waiting" in second["stage_label"]
    assert a.fastapi.state.runs.wait_idle(20)
    assert c.get(f"/api/runs/{first['run_id']}").json()["status"] == "done"
    a.fastapi.state.runs.stop()


def test_failed_run_has_plain_message(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver(fail_stage="snappyHexMesh")))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    s = wait_done(c, a, rid)
    assert s["status"] == "failed" and "mesh could not be fitted" in s["error"]
    assert "exited with code" not in s["error"]
    assert c.get(f"/api/runs/{rid}/surface").status_code == 404
    a.fastapi.state.runs.stop()


def test_results_survive_restart(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    wait_done(c, a, rid)
    a.fastapi.state.runs.stop()
    b = make_client(create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver())))
    s = b.get(f"/api/runs/{rid}").json()
    assert s["status"] == "done" and s["result"]["cl"] == pytest.approx(0.45)
    assert b.get(f"/api/runs/{rid}/surface").status_code == 200


@pytest.mark.parametrize("body, word", [
    ({"airspeed": 0.5}, "between 1 and 100 m/s"),
    ({"airspeed": 400}, "between 1 and 100 m/s"),
    ({"alpha_deg": 25}, "between -20 and 20 degrees"),
    ({"alpha_deg": "ten"}, "the angle of attack"),
    ({"preset": "detailed"}, "not available on this computer"),
    ({"preset": "huge"}, "not available on this computer"),
    ({"airspeed": None}, "airspeed"),
    ({"dictionary": "FoamFile {}"}, "dictionary"),
])
def test_run_rejects_bad_input(client, body, word):
    gid = naca(client).json()["geometry_id"]
    r = client.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0, **body})
    assert r.status_code in (400, 422), r.text
    assert word in r.json()["detail"]


def test_run_rejects_unknown_or_bad_geometry(client):
    r = client.post("/api/runs", json={"geometry_id": str(uuid.uuid4()), "airspeed": 20, "alpha_deg": 0})
    assert r.status_code == 400 and "not found" in r.json()["detail"]
    r = client.post("/api/runs", json={"geometry_id": "../../etc/passwd", "airspeed": 20, "alpha_deg": 0})
    assert r.status_code == 400


def test_suspicious_units_need_confirmation(client, app, tmp_path):
    g = client.post("/api/geometry", params={"units": "m"}, content=cube_bytes(tmp_path, scale=30.0)).json()
    assert g["size_suspicious"] and g["suggested_units"]
    r = client.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0})
    assert r.status_code == 400 and "units" in r.json()["detail"]
    r = client.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 0,
                                       "units_confirmed": True})
    assert r.status_code == 202  # the student confirmed that the 30 m size is right
    assert app.fastapi.state.runs.wait_idle(20)


def test_bad_run_ids(client):
    assert client.get("/api/runs/abc").status_code == 400
    assert client.get(f"/api/runs/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/api/runs/{uuid.uuid4()}/surface").status_code == 404


# --------------------------------------------------------------------------- guards


def test_foreign_host_is_refused(client):
    r = client.get("/api/config", headers={"host": "evil.example:8080"})
    assert r.status_code == 421
    for h in ("localhost:8090", "127.0.0.1:8080", "[::1]:8080"):
        assert client.get("/api/config", headers={"host": h}).status_code == 200


def test_cross_origin_post_is_refused(client):
    body = {"code": "0012", "chord": 0.3, "span": 0.6}
    r = client.post("/api/geometry/naca", json=body, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/geometry/naca", json=body, headers={"origin": "null"})
    assert r.status_code == 403
    r = client.post("/api/geometry/naca", json=body, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/api/geometry/naca", json=body, headers={"origin": "http://localhost:8090"})
    assert r.status_code == 200


def test_large_json_body_is_refused(client):
    r = client.post("/api/geometry/naca", json={"code": "0012", "chord": 0.3, "span": 0.6, "x": "a" * 100_000})
    assert r.status_code == 413


def test_allowed_extra_host(tmp_path, solver):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=solver, allowed_hosts=["windlab.local"]))
    c = make_client(a)
    assert c.get("/api/config", headers={"host": "windlab.local:8080"}).status_code == 200
