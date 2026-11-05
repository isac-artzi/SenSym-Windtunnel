"""Convergence stop rule (windlab.runner.ConvergenceMonitor) on synthetic and recorded histories."""

from __future__ import annotations

import csv
import gzip
from pathlib import Path

import numpy as np
import pytest

from windlab.airfoil2d import VALIDATION_CONVERGENCE, ZERO_ALPHA_CONVERGENCE
from windlab.airfoil2d import case_parameters as params_2d
from windlab.case_builder import (
    EVERYDAY_CONVERGENCE,
    FlowConditions,
    GeometryInput,
    ReferenceValues,
    build_sweep_case,
    case_parameters,
    freestream_directions,
    residual_guard_fields,
)
from windlab.geometry.naca import naca_wing
from windlab.runner import ConvergenceMonitor

RECORDED = Path(__file__).with_name("data") / "convergence"
RESIDUAL_FIELDS = ("Ux", "Uy", "Uz", "p", "k", "omega")


def make_case(tmp_path, cl, cd, residual=1e-2):
    case = tmp_path / "case"
    d = case / "postProcessing" / "forceCoeffs1" / "0"
    d.mkdir(parents=True)
    rows = "\n".join(f"{i + 1} {b:.8f} {a:.8f}" for i, (a, b) in enumerate(zip(cl, cd, strict=True)))
    (d / "coefficient.dat").write_text("# Time Cd Cl\n" + rows + "\n")
    s = case / "postProcessing" / "solverInfo1" / "0"
    s.mkdir(parents=True)
    (s / "solverInfo.dat").write_text(f"# Time Ux_initial Uz_initial p_initial\n1 {residual} {residual} {residual}\n")
    (case / "system").mkdir()
    (case / "system" / "controlDict").write_text("application simpleFoam;\nstopAt          endTime;\n")
    return case


def zero_alpha_history(n):
    """CL oscillating around zero with slowly decaying amplitude, CD creeping up then flat."""
    i = np.arange(n)
    cl = 0.04 * np.exp(-i / 1500) * np.sin(2 * np.pi * i / 700)
    cd = 0.00807 - 0.0003 * np.exp(-i / 900)
    return cl, cd


def test_alpha_zero_uses_cd_rule():
    assert params_2d(0.0, 1)["convergence"] == ZERO_ALPHA_CONVERGENCE
    assert params_2d(10.0, 1)["convergence"] == VALIDATION_CONVERGENCE


def test_alpha_zero_history_stops_on_cd_with_high_residuals(tmp_path):
    cl, cd = zero_alpha_history(6000)
    case = make_case(tmp_path, cl, cd, residual=1.4e-2)
    strict = ConvergenceMonitor(case, **VALIDATION_CONVERGENCE)
    loose = ConvergenceMonitor(case, **ZERO_ALPHA_CONVERGENCE)
    # The old rule never fires on this history (residuals 1e-2 and CL "not settled").
    assert not any(strict.settled(cl[:n], cd[:n]) for n in range(1500, 6001, 250))
    first = next(n for n in range(800, 6001, 10) if loose.settled(cl[:n], cd[:n]))
    assert 1500 < first < 5000
    assert abs(cd[first - 200 : first].mean() / cd[-200:].mean() - 1) < 0.005


def test_alpha_zero_rule_waits_for_its_minimum(tmp_path):
    """A history that looks settled early (the 2026-11-03 live run flattened at 1200-1500
    iterations, 2 % low) must not stop before the 2500-iteration minimum."""
    assert ZERO_ALPHA_CONVERGENCE["min_iterations"] == 2500
    cl, cd = np.full(2400, 0.001), np.full(2400, 0.0079)
    mon = ConvergenceMonitor(make_case(tmp_path, cl, cd), **ZERO_ALPHA_CONVERGENCE)
    mon.check()
    assert mon.stopped_at is None
    cl, cd = np.full(2600, 0.001), np.full(2600, 0.0079)
    mon = ConvergenceMonitor(make_case(tmp_path / "b", cl, cd), **ZERO_ALPHA_CONVERGENCE)
    mon.check()
    assert mon.stopped_at == 2600


def test_alpha_zero_rule_ignores_the_half_model_lift(tmp_path):
    """The half model's CL is the upper surface's own (large, meaningless); only CD counts."""
    cl = np.full(3000, 0.45)
    cd = np.full(3000, 0.008)
    case = make_case(tmp_path, cl, cd)
    assert ConvergenceMonitor(case, **ZERO_ALPHA_CONVERGENCE).settled(cl, cd)
    assert not ConvergenceMonitor(case, **ZERO_ALPHA_CONVERGENCE).settled(cl, np.linspace(0.009, 0.008, 3000))


def test_check_writes_stop_at(tmp_path):
    cl, cd = np.full(3000, 0.001), np.full(3000, 0.008)
    case = make_case(tmp_path, cl, cd)
    mon = ConvergenceMonitor(case, **ZERO_ALPHA_CONVERGENCE)
    mon.check()
    assert mon.stopped_at == 3000
    assert "stopAt          writeNow;" in (case / "system" / "controlDict").read_text()


def test_default_rule_needs_low_residuals(tmp_path):
    cl, cd = np.full(1000, 0.5), np.full(1000, 0.01)
    assert not ConvergenceMonitor(make_case(tmp_path, cl, cd, residual=1e-2)).settled(cl, cd)
    assert ConvergenceMonitor(make_case(tmp_path / "b", cl, cd, residual=1e-6)).settled(cl, cd)


# --------------------------------------------------------------------------- residual guard (task 007, step 1)


def test_guard_fields_follow_the_freestream():
    u = lambda a: 20 * freestream_directions(a)[0]  # noqa: E731
    assert residual_guard_fields(u(0)) == ["p", "k", "omega", "Ux"]  # Uy and Uz are ~0 everywhere
    assert residual_guard_fields(u(2)) == ["p", "k", "omega", "Ux"]  # sin 2 deg = 3.5 % < 5 %
    assert residual_guard_fields(u(10)) == ["p", "k", "omega", "Ux", "Uz"]
    assert residual_guard_fields(u(-10)) == ["p", "k", "omega", "Ux", "Uz"]
    assert residual_guard_fields([0, 0, 0]) == ["p", "k", "omega"]


def test_every_case_carries_its_guard_fields(tmp_path):
    w = naca_wing("0012", chord=0.3, span=0.6)
    p = tmp_path / "w.stl"
    w.write_stl(p)
    geom = GeometryInput(stl_path=p, bounds_min=tuple(w.vertices.min(0)), bounds_max=tuple(w.vertices.max(0)))
    ref = ReferenceValues(area=0.18, length=0.3)
    assert case_parameters(geom, FlowConditions(airspeed=20, alpha_deg=0), ref, "quick", 2)["convergence"] == {
        **EVERYDAY_CONVERGENCE, "guard_fields": ["p", "k", "omega", "Ux"]}
    # a sweep gives each angle its own guard (the runner merges the point into the case meta)
    case = build_sweep_case(geom, 20.0, [0, 10], ref, runs_root=tmp_path / "runs")
    import json
    pts = json.loads((case.path / "windlab_case.json").read_text())["sweep"]["points"]
    assert [pt["convergence"]["guard_fields"] for pt in pts] == [["p", "k", "omega", "Ux"],
                                                                ["p", "k", "omega", "Ux", "Uz"]]


def recorded(name: str) -> dict[str, np.ndarray]:
    """A history recorded from a real run (CL, CD and the initial residuals per iteration)."""
    with gzip.open(RECORDED / f"{name}.csv.gz", "rt") as fh:
        rows = [r for r in csv.reader(line for line in fh if not line.startswith("#"))]
    head, body = rows[0], rows[1:]
    cols = {h: np.array([r[i] or "nan" for r in body], dtype=float) for i, h in enumerate(head)}
    return cols


def write_residuals(case: Path, hist: dict, n: int) -> None:
    """solverInfo.dat with the residuals as they stood after iteration ``n`` (the monitor reads the last row)."""
    s = case / "postProcessing" / "solverInfo1" / "0"
    i = n - 1
    s.mkdir(parents=True, exist_ok=True)
    (s / "solverInfo.dat").write_text("# Time " + " ".join(f"{f}_initial" for f in RESIDUAL_FIELDS) + "\n" +
                                      f"{n} " + " ".join(f"{hist[f][i]:.3e}" for f in RESIDUAL_FIELDS) + "\n")


def first_stop(case: Path, hist: dict, mon: ConvergenceMonitor, step: int = 5) -> int | None:
    cl, cd = hist["Cl"], hist["Cd"]
    for n in range(mon.window + mon.lag, len(cl) + 1, step):
        write_residuals(case, hist, n)
        if mon.settled(cl[:n], cd[:n]):
            return n
    return None


@pytest.mark.parametrize("name, old_stop, limit", [
    ("wing_fine_alpha0", None, 600),  # 3.4 M cells: never fired, ran to the cap (Uy stuck at 1.4e-4)
    ("wing_standard_alpha0", 982, 500),  # 0.8 M cells: fired only when Uy dipped under 1e-4 at 970
    ("wing_quick_alpha0", 330, 400),  # Quick: Uy happened to drop below 1e-4 at 238
])
def test_recorded_zero_incidence_histories_stop_sensibly(tmp_path, name, old_stop, limit):
    hist = recorded(name)
    cl, cd = hist["Cl"], hist["Cd"]
    case = make_case(tmp_path, cl, cd)
    guard = residual_guard_fields(20 * freestream_directions(0)[0])
    # the old rule (every residual) reproduces what happened in the mesh study (the live monitor
    # looked every ~2 s, i.e. every ~10 iterations, so the recorded stop is a little later)
    old = first_stop(case, hist, ConvergenceMonitor(case))
    assert old == (None if old_stop is None else pytest.approx(old_stop, abs=20))
    new = first_stop(case, hist, ConvergenceMonitor(case, guard_fields=guard))
    assert new is not None and new <= limit, new
    # ... and the drag it stops on is the run's long-run value
    assert abs(cd[new - 200:new].mean() / cd[-200:].mean() - 1) < 0.005
    # the guard still bites: with the pressure residual stuck high the rule does not fire
    stuck = {**hist, "p": np.full_like(hist["p"], 1e-3)}
    assert first_stop(case, stuck, ConvergenceMonitor(case, guard_fields=guard)) is None


def test_recorded_lifting_case_unchanged(tmp_path):
    """At 10 deg every component carries flow; old and new rule agree."""
    hist = recorded("wing_quick_alpha10")
    case = make_case(tmp_path, hist["Cl"], hist["Cd"])
    guard = residual_guard_fields(20 * freestream_directions(10)[0])
    old = first_stop(case, hist, ConvergenceMonitor(case))
    new = first_stop(case, hist, ConvergenceMonitor(case, guard_fields=guard))
    assert old == new and 300 <= new <= 400


# --------------------------------------------------------------------------- everyday rule (task 010, step 3)


def everyday(case, alpha):
    guard = residual_guard_fields(20 * freestream_directions(alpha)[0])
    return ConvergenceMonitor(case, guard_fields=guard, **EVERYDAY_CONVERGENCE)


@pytest.mark.parametrize("name, alpha, latest, old_rule_stop", [
    ("wing_quick_alpha10", 10, 260, 375),
    ("wing_quick_alpha0", 0, 200, 320),
    ("wing_standard_alpha0", 0, 200, 330),
    ("wing_fine_alpha0", 0, 260, 355),
])
def test_everyday_rule_stops_earlier_with_the_same_answer(tmp_path, name, alpha, latest, old_rule_stop):
    """The 100/50 window with 0.25 % / 0.5 % tolerances fires 100-150 iterations before the old
    200/100 rule and the CL / CD it reports (means over its window) are the runs' long-run values."""
    hist = recorded(name)
    cl, cd = hist["Cl"], hist["Cd"]
    case = make_case(tmp_path, cl, cd)
    mon = everyday(case, alpha)
    assert mon.window == 100 and mon.lag == 50 and mon.window + mon.lag == 150  # the earliest possible stop
    stop = first_stop(case, hist, mon)
    assert stop is not None and 150 <= stop <= latest, stop
    assert stop < old_rule_stop - 80
    w = mon.window
    assert abs(cd[stop - w:stop].mean() / cd[-200:].mean() - 1) < 0.002
    if abs(cl[-200:].mean()) > 0.05:  # at zero incidence CL is noise around zero
        assert abs(cl[stop - w:stop].mean() / cl[-200:].mean() - 1) < 0.001


def test_everyday_rule_falls_back_to_the_long_window_for_slow_oscillations(tmp_path):
    """A CL swinging slowly by 2 % never satisfies the 100/50 rule, but the old 200/100 rule
    (kept as the fallback) accepts it once its longer means agree - as it did before task 010."""
    n = 1500
    i = np.arange(n)
    # period 200: a 200-iteration mean is flat (whole periods), while 100-iteration means (half
    # a period) still swing by 2/pi of the amplitude; two of them 50 apart agree only at
    # isolated instants, and with CD swinging out of phase those instants never coincide
    cl = 0.53 * (1 + 0.02 * np.sin(2 * np.pi * i / 200))
    cd = 0.078 * (1 + 0.03 * np.sin(2 * np.pi * i / 200 + 1.0))
    case = make_case(tmp_path, cl, cd, residual=1e-6)
    mon = everyday(case, 12)
    assert mon.fallback is not None and mon.fallback.window == 200
    only_new = ConvergenceMonitor(case, guard_fields=mon.guard_fields,
                                  **{k: v for k, v in EVERYDAY_CONVERGENCE.items() if k != "fallback"})
    assert not any(only_new.settled(cl[:k], cd[:k]) for k in range(150, n + 1, 5))
    old = ConvergenceMonitor(case)
    first_old = next((k for k in range(300, n + 1, 5) if old.settled(cl[:k], cd[:k])), None)
    first = next((k for k in range(150, n + 1, 5) if mon.settled(cl[:k], cd[:k])), None)
    assert first is not None and first == first_old == 300
    assert mon.fired_window == 200  # the result is then averaged over the longer window
    # the residual guard still applies to the fallback
    assert not everyday(make_case(tmp_path / "r", cl, cd, residual=1e-2), 12).settled(cl, cd)


def test_everyday_rule_is_as_strict_per_iteration_as_the_old_one(tmp_path):
    """Half the window and half the tolerance: the same drift per 100 iterations is allowed, so a
    CL still creeping 0.6 % per 100 iterations does not pass either rule."""
    n = 2000
    i = np.arange(n)
    cl = 0.5 * (1 + 0.006 * i / 100)  # +0.6 % per 100 iterations, forever
    cd = np.full(n, 0.05)
    case = make_case(tmp_path, cl, cd, residual=1e-6)
    assert not everyday(case, 10).settled(cl, cd)
    assert not ConvergenceMonitor(case).settled(cl, cd)
    flat = np.full(n, 0.5)
    assert everyday(case, 10).settled(flat, cd)
