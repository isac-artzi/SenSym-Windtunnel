"""Mid-span flow slice: template entry, reader/encoder, API."""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca, write_slice_vtp
from windlab.app import Settings, create_app
from windlab.app import slice as fs
from windlab.case_builder import FlowConditions, GeometryInput, ReferenceValues, build_case
from windlab.geometry.naca import naca_wing


def test_case_has_a_bounded_mid_span_cutting_plane(tmp_path):
    w = naca_wing("0012", chord=0.3, span=0.6)
    p = tmp_path / "w.stl"
    w.write_stl(p)
    lo, hi = w.vertices.min(0), w.vertices.max(0)
    g = GeometryInput(stl_path=p, bounds_min=tuple(lo), bounds_max=tuple(hi))
    case = build_case(g, FlowConditions(airspeed=20, alpha_deg=4), ReferenceValues(area=0.18, length=0.3),
                      n_procs=2, runs_root=tmp_path / "runs")
    ctrl = (case.path / "system" / "controlDict").read_text()
    assert "type        cuttingPlane;" in ctrl and "normal  (0 1 0);" in ctrl and "interpolate true;" in ctrl
    y = case.params["slice_point"][1]
    assert y == pytest.approx(0.5 * (lo[1] + hi[1]), abs=1e-3) and y != 0.5 * (lo[1] + hi[1])  # nudged off faces
    # the cut spans the whole tunnel (the page crops a close-up); the body box is recorded
    (bmin, bmax) = case.params["slice_bounds"]
    assert bmin == pytest.approx(case.params["domain_min"]) and bmax == pytest.approx(case.params["domain_max"])
    assert case.params["body_min"] == pytest.approx(lo.tolist())
    assert case.params["body_max"] == pytest.approx(hi.tolist())
    assert fs.body_box(case.params) == (case.params["body_min"], case.params["body_max"])
    win = fs.slice_info(case.params, [bmin[0], bmax[0], bmin[2], bmax[2]], y)["windows"]["closeup"]
    assert win == pytest.approx([lo[0] - 0.15, hi[0] + 0.45, lo[2] - 0.18, hi[2] + 0.18])


def test_body_box_of_older_cases_comes_from_the_slice_bounds():
    old = {"l_ref": 0.3, "slice_point": [0.15, 8e-6, 0.0],
           "slice_bounds": [[-0.15, -6.6, -0.198], [0.75, 6.6, 0.198]]}
    lo, hi = fs.body_box(old)
    assert lo == pytest.approx([0.0, 8e-6, -0.018]) and hi == pytest.approx([0.3, 8e-6, 0.018])


def test_slice_round_trip(tmp_path):
    write_slice_vtp(tmp_path / "postProcessing" / "midPlane" / "300" / "plane.vtp", 20.0)
    d = fs.decode(fs.flow_slice(tmp_path, 20.0))
    assert d["xz"].shape == (55, 2) and d["triangles"].shape == (80, 3)
    assert d["speed_max"] == pytest.approx(1.3, rel=0.01) and d["speed"].min() == pytest.approx(1.0, abs=1e-3)
    # Cp from Bernoulli in the fake data: Cp = 1 - (U / U_inf)^2
    assert np.allclose(d["cp"], 1 - d["speed"] ** 2, atol=1e-4)
    assert (d["x_min"], d["x_max"]) == pytest.approx((-0.2, 0.8)) and d["y"] == 0


def test_slice_missing_or_damaged(tmp_path):
    with pytest.raises(fs.SurfaceError):
        fs.flow_slice(tmp_path, 20.0)
    f = tmp_path / "postProcessing" / "midPlane" / "5" / "plane.vtp"
    f.parent.mkdir(parents=True)
    f.write_text("<VTKFile>nothing</VTKFile>")
    with pytest.raises(fs.SurfaceError):
        fs.flow_slice(tmp_path, 20.0)


def test_slice_api_single_and_sweep(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    gid = naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 4}).json()["run_id"]
    sid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep",
                                    "angles": [0, 8]}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    s = c.get(f"/api/runs/{rid}").json()
    assert s["slice_available"] and s["warnings"] == []
    r = c.get(f"/api/runs/{rid}/slice")
    assert r.status_code == 200 and fs.decode(r.content)["triangles"].shape == (80, 3)
    j = c.get(f"/api/runs/{rid}/slice", params={"format": "json"}).json()
    assert len(j["speed"]) == 55 and j["speed_max"] == pytest.approx(1.3, rel=0.01)
    assert c.get(f"/api/runs/{sid}").json()["slice_points"] == [0, 1]
    assert c.get(f"/api/runs/{sid}/slice", params={"point": 1}).status_code == 200
    assert c.get(f"/api/runs/{sid}/slice", params={"point": 5}).status_code == 400
    info = c.get(f"/api/runs/{rid}/slice-info").json()
    assert set(info["windows"]) == {"closeup", "tunnel"} and info["body_size"] > 0
    assert set(info["streamlines"]) == {"closeup", "tunnel"} and info["streamlines"]["tunnel"]
    assert c.get(f"/api/runs/{sid}/slice-info", params={"point": 1}).json()["windows"]["tunnel"]
    assert c.get(f"/api/runs/{sid}/slice-info", params={"point": 7}).status_code == 400
    # the raw OpenFOAM slice is gone after slimming; the WindLab picture stays
    case = a.fastapi.state.runs.root / rid
    assert not (case / "postProcessing" / "midPlane").exists() and (case / "windlab_slice.bin").is_file()
    a.fastapi.state.runs.stop()
