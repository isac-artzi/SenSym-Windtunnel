"""End to end through the API with the real OpenFOAM solver (Quick preset, ~1-2 min).

Run with:  pytest -m slow
"""

from __future__ import annotations

import os
import time

import pytest

from tests.asgi_client import make_client
from windlab.app import Settings, create_app
from windlab.app import slice as flow_slice
from windlab.app import surface as surf
from windlab.runner import FOAM_BASHRC

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.path.isfile(FOAM_BASHRC), reason="OpenFOAM is not installed"),
]


def test_naca0012_quick_end_to_end(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", n_procs=4))
    c = make_client(app)
    g = c.post("/api/geometry/naca", json={"code": "0012", "chord": 0.3, "span": 0.6}).json()
    run = c.post("/api/runs", json={"geometry_id": g["geometry_id"], "airspeed": 20, "alpha_deg": 10}).json()
    seen_stages, seen_running = set(), False
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        s = c.get(f"/api/runs/{run['run_id']}").json()
        seen_stages.add(s["stage"])
        seen_running |= s["status"] == "running" and len(s["history"]) > 0
        if s["status"] in ("done", "failed"):
            break
        time.sleep(1.0)
    app.fastapi.state.runs.stop()
    assert s["status"] == "done", s.get("error")
    assert {"snappyHexMesh", "simpleFoam"} <= seen_stages and seen_running
    r = s["result"]
    assert 0.3 < r["cl"] < 0.7 and 0.01 < r["cd"] < 0.1, r  # 3D wing, AR 2: well below the 2D value
    assert 200_000 < r["cells"] < 800_000
    d = surf.decode(c.get(f"/api/runs/{run['run_id']}/surface").content)
    assert len(d["triangles"]) > 1000 and d["cp"] is not None
    assert 0.8 < d["cp_max"] < 1.2  # stagnation point
    # mid-span slice from OpenFOAM's cuttingPlane: air stops at the nose and speeds up over the top
    sl = flow_slice.decode(c.get(f"/api/runs/{run['run_id']}/slice").content)
    assert len(sl["triangles"]) > 1000 and 1.1 < sl["speed_max"] < 2.0 and 0.8 < sl["cp_max"] < 1.2
    assert "CL [-]" in c.get(f"/api/runs/{run['run_id']}/results.csv").text
    # bulky solver files are gone, the results stay
    case = app.fastapi.state.runs.root / run["run_id"]
    assert not list(case.glob("processor*")) and (case / "postProcessing" / "forceCoeffs1").is_dir()
