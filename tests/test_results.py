import numpy as np

from windlab import results
from windlab.runner import ConvergenceMonitor

COLUMNS = ["Time", "Cd", "Cd(f)", "Cd(r)", "Cl", "Cl(f)", "Cl(r)", "CmPitch", "CmRoll", "CmYaw", "Cs", "Cs(f)", "Cs(r)"]
COEFF_HEADER = "# Force and moment coefficients\n# " + "\t".join(COLUMNS) + "\n"


def _write_case(tmp_path, cl, cd, residual=1e-6):
    d = tmp_path / "postProcessing" / "forceCoeffs1" / "0"
    d.mkdir(parents=True)
    rows = "".join(
        f"{i + 1}\t{cd[i]}\t0\t0\t{cl[i]}\t0\t0\t0.01\t0\t0\t0\t0\t0\n" for i in range(len(cl))
    )
    (d / "coefficient.dat").write_text(COEFF_HEADER + rows + "999\t0.1")  # partial last line
    s = tmp_path / "postProcessing" / "solverInfo1" / "0"
    s.mkdir(parents=True)
    (s / "solverInfo.dat").write_text(
        "# Solver information\n# Time\tU_solver\tUx_initial\tUx_final\tp_solver\tp_initial\tp_final\n"
        f"1\tsmoothSolver\t{residual}\t1e-9\tGAMG\t{residual}\t1e-9\n"
    )
    ctrl = tmp_path / "system"
    ctrl.mkdir()
    (ctrl / "controlDict").write_text("application simpleFoam;\nstopAt          endTime;\nendTime 1500;\n")
    return tmp_path


def test_read_force_coeffs_and_summary(tmp_path):
    n = 300
    cl = 0.45 + 0.001 * np.sin(np.arange(n))
    case = _write_case(tmp_path, cl, np.full(n, 0.0558))
    c = results.read_force_coeffs(case)
    assert len(c["Time"]) == n  # partial line dropped
    s = results.summarize_coeffs(c)
    assert s["cl"] == np.float64(cl[-200:].mean())
    assert 0.0009 < s["cl_pm"] <= 0.001
    assert s["l_over_d"] == s["cl"] / s["cd"]
    assert results.final_residuals(case) == {"Ux": 1e-6, "p": 1e-6}


def test_monitor_stops_when_settled(tmp_path):
    n = 400
    case = _write_case(tmp_path, np.full(n, 0.45), np.full(n, 0.0558))
    mon = ConvergenceMonitor(case)
    mon.check()
    assert mon.stopped_at == n
    assert "stopAt          writeNow;" in (case / "system" / "controlDict").read_text()


def test_monitor_keeps_going_while_drifting(tmp_path):
    n = 400
    case = _write_case(tmp_path, np.linspace(0.30, 0.45, n), np.full(n, 0.0558))
    mon = ConvergenceMonitor(case)
    mon.check()
    assert mon.stopped_at is None
    assert "stopAt          endTime;" in (case / "system" / "controlDict").read_text()


def test_monitor_requires_low_residuals(tmp_path):
    n = 400
    case = _write_case(tmp_path, np.full(n, 0.45), np.full(n, 0.0558), residual=3e-3)
    mon = ConvergenceMonitor(case)
    mon.check()
    assert mon.stopped_at is None


def test_read_forces_and_split(tmp_path):
    d = tmp_path / "postProcessing" / "forces1" / "0"
    d.mkdir(parents=True)
    cols = ["Time", "total_x", "total_y", "total_z", "pressure_x", "pressure_y", "pressure_z",
            "viscous_x", "viscous_y", "viscous_z"]
    rows = "".join(f"{i + 1}\t1.0\t0\t2.0\t0.4\t0\t2.0\t0.6\t0\t0\n" for i in range(10))
    (d / "force.dat").write_text("# Force\n# CofR : (0 0 0)\n#\n# " + "\t".join(cols) + "\n" + rows)
    f = results.read_forces(tmp_path)
    assert len(f["pressure_x"]) == 10 and f["viscous_x"][0] == 0.6
    q = 0.5 * 1.225 * 20**2 * 0.18
    s = results.force_split(f, [1, 0, 0], [0, 0, 1], 1.225, 20.0, 0.18)
    assert abs(s["cd_pressure"] - 0.4 / q) < 1e-12 and abs(s["cd_viscous"] - 0.6 / q) < 1e-12
    assert abs(s["cl_pressure"] - 2.0 / q) < 1e-12 and s["cl_viscous"] == 0.0
    assert results.read_forces(tmp_path / "nowhere") is None


def test_parse_logs(tmp_path):
    (tmp_path / "log.checkMesh").write_text(
        "    cells:            371927\n    Mesh non-orthogonality Max: 64.99 average: 9.0\n"
        "    Max skewness = 2.3 OK.\n\nMesh OK.\n"
    )
    m = results.parse_check_mesh(tmp_path / "log.checkMesh")
    assert m == {"cells": 371927, "max_non_orthogonality": 64.99, "max_skewness": 2.3,
                 "failed_checks": 0, "mesh_ok": True}
    (tmp_path / "log.snappyHexMesh").write_text(
        "patch faces    layers        overall thickness\n"
        "              target   mesh     [m]       [%]\n"
        "----- -----    -----    ----     ---       ---\n"
        "body 32256     5        4.75     0.0055    96      \n\n\nDoing final balancing\n"
    )
    lay = results.parse_snappy_layers(tmp_path / "log.snappyHexMesh")["layers"]["body"]
    assert lay == {"faces": 32256, "target_layers": 5, "mean_layers": 4.75, "thickness_m": 0.0055,
                   "thickness_pct": 96.0}
