"""Export a finished run as a self-contained OpenFOAM case (RUN-3): GET /api/runs/{id}/case.zip."""

from __future__ import annotations

import io
import re
import zipfile

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from windlab import __version__
from windlab.app import Settings, create_app
from windlab.app import case_export as ce


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def _done_run(a, c, **body):
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, **body}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    return rid


def _zip(c, rid):
    r = c.get(f"/api/runs/{rid}/case.zip")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert re.fullmatch(r'attachment; filename="windlab-case-\d{4}-\d\d-\d\d-[0-9a-f]{8}\.zip"',
                        r.headers["content-disposition"])
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_single_run_case_zip_contents(env):
    a, c = env
    rid = _done_run(a, c, alpha_deg=10)
    z = _zip(c, rid)
    names = z.namelist()
    root = f"windlab-case-{rid[:8]}/"
    assert all(n.startswith(root) for n in names)
    rel = {n[len(root):] for n in names}
    for must in ("system/controlDict", "system/snappyHexMeshDict", "system/fvSolution", "constant/triSurface/body.stl",
                 "constant/turbulenceProperties", "0.orig/U", "0/U", "0/p", "Allrun", "README.md",
                 "NOTICE-OPENFOAM.txt", "windlab_case.json", "postProcessing/forceCoeffs1/0/coefficient.dat"):
        assert must in rel, must
    # nothing bulky or unsafe: no processor folders, no solution time directories, no mesh, no parent paths
    assert not any(p.startswith("processor") or "/processor" in p for p in rel)
    assert not any(re.match(r"^\d+/", p) and not p.startswith("0/") for p in rel)
    assert not any("polyMesh" in p or ".." in p or p.startswith("/") for p in rel)
    # the Allrun is executable, uses only OpenFOAM's own tools, and carries no user text
    info = z.getinfo(root + "Allrun")
    assert (info.external_attr >> 16) & 0o111
    script = z.read(root + "Allrun").decode()
    for cmd in ("blockMesh", "surfaceFeatureExtract", "decomposePar", "snappyHexMesh -overwrite", "potentialFoam",
                "simpleFoam", "restore0Dir", "RunFunctions"):
        assert cmd in script, cmd
    assert "NACA" not in script and "for pair in" not in script
    readme = z.read(root + "README.md").decode()
    assert __version__ in readme and "commit" in readme and "NACA 0012" in readme
    assert "angle of attack: 10 deg" in readme and "CL = 0.45" in readme and "not yet validated" in readme
    assert "No solution time directories" in readme
    notice = z.read(root + "NOTICE-OPENFOAM.txt").decode()
    assert "GNU GPL v3" in notice and "registered trade mark of OpenCFD Limited" in notice
    assert ("not approved or endorsed by OpenCFD Limited, producer and distributor of\nthe OpenFOAM software via "
            "www.openfoam.com, and owner of the OPENFOAM(R) and OpenCFD(R) trade\nmarks.") in notice
    assert "developer using" not in notice and "developer using" not in readme  # the odd phrase is gone
    # the dictionaries are the ones WindLab ran
    ctrl = z.read(root + "system/controlDict").decode()
    assert "forceCoeffs" in ctrl and "#codeStream" not in ctrl


def test_sweep_case_zip_loops_over_angles(env):
    a, c = env
    rid = _done_run(a, c, kind="sweep", angles=[-4, 4, 12])
    z = _zip(c, rid)
    root = f"windlab-case-{rid[:8]}/"
    rel = {n[len(root):] for n in z.namelist()}
    assert {"sweep/p0/controlDict", "sweep/p0/U", "sweep/p2/U",
            "sweep/p1/postProcessing/forceCoeffs1/0/coefficient.dat"} <= rel
    script = z.read(root + "Allrun").decode()
    assert "for pair in 0:-4 1:4 2:12; do" in script and "sweep/p$i/controlDict" in script
    readme = z.read(root + "README.md").decode()
    assert "angles of attack: -4, 4, 12 deg" in readme and "| 4 |" in readme


def test_unfinished_or_missing_runs_are_refused(env, tmp_path):
    a, c = env
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    assert c.get(f"/api/runs/{rid}/case.zip").status_code == 200
    assert c.get("/api/runs/not-a-run/case.zip").status_code == 400
    assert c.get("/api/runs/11111111-2222-4333-8444-555555555555/case.zip").status_code == 404
    # a failed run has nothing complete to export
    b = create_app(Settings(data_dir=tmp_path / "f", runner=FakeSolver(fail_stage="snappyHexMesh")))
    cb = make_client(b)
    g2 = naca(cb).json()["geometry_id"]
    r2 = cb.post("/api/runs", json={"geometry_id": g2, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert b.fastapi.state.runs.wait_idle(20)
    r = cb.get(f"/api/runs/{r2}/case.zip")
    assert r.status_code == 409 and "finished" in r.json()["detail"]
    b.fastapi.state.runs.stop()


def test_size_cap(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver(), export_cap_bytes=10_000))
    c = make_client(a)
    rid = _done_run(a, c, alpha_deg=0)
    r = c.get(f"/api/runs/{rid}/case.zip")
    assert r.status_code == 409 and "MB" in r.json()["detail"]
    a.fastapi.state.runs.stop()


def test_export_skips_symlinks_processor_and_time_dirs(tmp_path):
    case = tmp_path / "case"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("application simpleFoam;\n")
    (case / "constant" / "triSurface").mkdir(parents=True)
    (case / "constant" / "triSurface" / "body.stl").write_bytes(b"\0" * 100)
    (case / "constant" / "polyMesh").mkdir()
    (case / "constant" / "polyMesh" / "points").write_bytes(b"\0" * 100)
    (case / "0.orig").mkdir()
    (case / "0.orig" / "U").write_text("U")
    (case / "processor0" / "300").mkdir(parents=True)
    (case / "processor0" / "300" / "U").write_bytes(b"\0" * 100)
    (case / "300").mkdir()
    (case / "300" / "U").write_bytes(b"\0" * 100)
    (case / "postProcessing" / "forceCoeffs1" / "0").mkdir(parents=True)
    (case / "postProcessing" / "forceCoeffs1" / "0" / "coefficient.dat").write_text("# Time Cd Cl\n1 0.1 0.2\n")
    (case / "windlab_case.json").write_text('{"n_layers": 5, "background_cell": 0.6, "surface_cell": 0.002}')
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (case / "system" / "link").symlink_to(secret)
    status = {"run_id": "abcdef12-0000-4000-8000-000000000000", "params": {"kind": "single", "n_procs": 2,
              "airspeed": 20.0, "alpha_deg": 0.0, "reynolds": 4e5, "reference_area": 0.18, "reference_length": 0.3},
              "result": {"cl": 0.0, "cd": 0.017, "iterations": 300, "converged": True}}
    z = zipfile.ZipFile(io.BytesIO(ce.export_case(case, status, commit="deadbeef")))
    rel = {n.split("/", 1)[1] for n in z.namelist()}
    assert "system/controlDict" in rel and "0/U" in rel and "postProcessing/forceCoeffs1/0/coefficient.dat" in rel
    assert "system/link" not in rel and not any("polyMesh" in p or "processor" in p or p.startswith("300") for p in rel)
    assert "commit deadbeef" in z.read("windlab-case-abcdef12/README.md").decode()


def test_commit_lookup_is_safe(monkeypatch):
    monkeypatch.setenv("WINDLAB_COMMIT", "abc123; rm -rf /")
    assert ce.windlab_commit() == "abc123; rm -rf /"[:40]  # only ever written into a README line
    monkeypatch.delenv("WINDLAB_COMMIT")
    sha = ce.windlab_commit()
    assert sha == "unknown" or re.fullmatch(r"[0-9a-f]{7,40}", sha)


def test_page_offers_the_download():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "windlab" / "app" / "static" / "index.html").read_text()
    assert 'id="dl-case"' in html and "OpenFOAM case (.zip)" in html
