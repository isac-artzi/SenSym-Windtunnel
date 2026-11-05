"""Time-left estimator (windlab.app.eta): priors, measured rate, conservative early, sweeps."""

from __future__ import annotations

import pytest

from windlab.app import eta
from windlab.app.eta import EtaInput, estimate, expected_iterations, seconds_left, time_left_text

STAGES = ["blockMesh", "surfaceFeatureExtract", "decomposePar", "snappyHexMesh", "checkMesh", "potentialFoam",
          "simpleFoam", "surface"]


def timeline(k: float, iterations: int = 400, n_points: int = 1):
    """A simulated run on a machine ``k`` times slower than the reference (no safety factor).

    Yields (EtaInput, true seconds left) every second."""
    ref = eta.PRIOR_SECONDS
    rate = eta.PRIOR_ITERATIONS / (ref["simpleFoam"] * k)  # iterations per second
    plan = [(s, ref[s] * k) for s in STAGES[:5]]
    for _ in range(n_points):
        plan += [("potentialFoam", ref["potentialFoam"] * k), ("simpleFoam", iterations / rate),
                 ("surface", ref["surface"] * k)]
    total = sum(d for _, d in plan)
    t, done, point, finished_its, finished_secs = 0.0, {}, -1, [], []
    for i, (stage, dur) in enumerate(plan):
        if stage == "potentialFoam":
            point += 1
            point_start = t
        start = t
        while t < start + dur:
            it = int((t - start) * rate) if stage == "simpleFoam" else 0
            yield EtaInput(status="running", stage=stage, now=t, started=0.0, stage_started=start,
                           stage_times=dict(done), iteration=it, max_iterations=1500, n_procs=4,
                           n_points=n_points, point_index=max(point, 0), finished_point_iterations=list(finished_its),
                           finished_point_seconds=list(finished_secs)), total - t
            t += 1.0
        t = start + dur
        done[stage] = done.get(stage, 0.0) + dur
        if stage == "surface":
            finished_its.append(iterations)
            finished_secs.append(t - point_start)
        _ = i


@pytest.mark.parametrize("k", [0.5, 1.0, 2.0])
def test_never_underestimates_early_on_machines_up_to_the_safety_factor(k):
    for inp, true_left in timeline(k):
        if inp.now > 0.4 * (true_left + inp.now):  # first 40 % of the run
            break
        assert seconds_left(inp) >= 0.95 * true_left, (inp.stage, inp.now, seconds_left(inp), true_left)


@pytest.mark.parametrize("k", [0.5, 1.0, 2.0, 4.0])
def test_estimate_settles_close_to_the_truth_once_the_solver_rate_is_known(k):
    checked = 0
    rate = eta.PRIOR_ITERATIONS / (eta.PRIOR_SECONDS["simpleFoam"] * k)
    # the stop rule fires at 400 here but the estimator expects up to 500: that much over is by design
    slack = (eta.EXPECTED_ITERATIONS - 400) / rate
    for inp, true_left in timeline(k):
        if inp.stage == "simpleFoam" and inp.iteration >= eta.MIN_ITERATIONS_FOR_RATE and true_left > 20:
            est = seconds_left(inp)
            assert 0.8 * true_left <= est <= 1.5 * true_left + slack + 15, (k, inp.iteration, est, true_left)
            checked += 1
    assert checked > 0


def test_slow_machine_estimate_grows_after_meshing():
    """Four times slower than the reference: meshing reveals it and the estimate goes up."""
    seen = {}
    for inp, _true in timeline(4.0):
        seen.setdefault(inp.stage, seconds_left(inp))
    assert seen["potentialFoam"] > seen["blockMesh"] * 0.9  # the speed factor rescaled the solver prior


def test_speed_factor_needs_meshing_to_be_done():
    assert eta.speed_factor({}, 4) == 1.0
    assert eta.speed_factor({"blockMesh": 50.0}, 4) == 1.0  # one tiny stage is not enough evidence
    slow = {"blockMesh": 6, "surfaceFeatureExtract": 6, "decomposePar": 6, "snappyHexMesh": 66}
    assert eta.speed_factor(slow, 4) == pytest.approx(84 / (14 * eta.PRIOR_SAFETY))
    fast = {"blockMesh": 0.5, "surfaceFeatureExtract": 0.5, "decomposePar": 0.5, "snappyHexMesh": 5.5}
    f = eta.speed_factor(fast, 4)
    assert 0.3 <= f < 1 and f > 7 / (14 * eta.PRIOR_SAFETY)  # a fast machine is only partly trusted


def test_rate_is_held_to_the_prior_with_few_iterations():
    prior_rate = eta.PRIOR_ITERATIONS / eta.prior("simpleFoam", 4)
    # a burst of fast early iterations doesn't make the estimate optimistic
    inp = EtaInput(status="running", stage="simpleFoam", now=12.0, stage_started=10.0, iteration=100)
    assert eta.iteration_rate(inp, 1.0) == pytest.approx(prior_rate)
    # a slow start is believed at once
    inp = EtaInput(status="running", stage="simpleFoam", now=60.0, stage_started=10.0, iteration=20)
    assert eta.iteration_rate(inp, 1.0) == pytest.approx(20 / 50)
    # enough iterations: the measured rate
    inp = EtaInput(status="running", stage="simpleFoam", now=40.0, stage_started=10.0, iteration=600)
    assert eta.iteration_rate(inp, 1.0) == pytest.approx(20.0)


def test_expected_iterations():
    assert expected_iterations(0, 1500) == eta.EXPECTED_ITERATIONS
    assert expected_iterations(450, 1500) == 562  # past the prior: another quarter
    assert expected_iterations(1450, 1500) == 1500  # never above the cap
    assert expected_iterations(600, 1500, converged=True) == 600
    xs = [expected_iterations(i, 1500) for i in range(0, 1500, 50)]
    assert xs == sorted(xs)


def test_converged_run_has_only_the_pictures_left():
    inp = EtaInput(status="running", stage="simpleFoam", now=100.0, started=0.0, stage_started=20.0,
                   stage_times={"snappyHexMesh": 11}, iteration=380, converged=True)
    assert seconds_left(inp) < 10


def test_sweep_counts_the_angles_still_to_come():
    base = dict(status="running", stage="simpleFoam", now=100.0, started=0.0, stage_started=50.0, iteration=200,
                stage_times={"blockMesh": 1, "surfaceFeatureExtract": 1, "decomposePar": 1, "snappyHexMesh": 22,
                             "checkMesh": 2})
    one = seconds_left(EtaInput(**base))
    three = seconds_left(EtaInput(**base, n_points=3, point_index=0))
    assert three > 2.5 * one
    # measured angle times dominate when they are longer than the model
    late = seconds_left(EtaInput(**base, n_points=3, point_index=1, finished_point_iterations=[900],
                                 finished_point_seconds=[400.0]))
    assert late >= 400


def test_cached_mesh_and_warm_start_stages():
    """A reused mesh leaves only the solve; the warm-start stage counts like potentialFoam."""
    reuse = seconds_left(EtaInput(status="running", stage="meshReuse", now=1.0, started=0.0, stage_started=0.0))
    cold = seconds_left(EtaInput(status="running", stage="blockMesh", now=1.0, started=0.0, stage_started=0.0))
    assert 0 < reuse < cold - 0.5 * eta.prior("snappyHexMesh", 4)
    warm = seconds_left(EtaInput(status="running", stage="changeDictionary", now=30.0, started=0.0,
                                 stage_started=29.0, stage_times={"meshReuse": 2.0}))
    pot = seconds_left(EtaInput(status="running", stage="potentialFoam", now=30.0, started=0.0,
                                stage_started=29.0, stage_times={"meshReuse": 2.0}))
    assert warm == pytest.approx(pot) and warm > eta.EXPECTED_ITERATIONS / 10


def test_estimate_output_and_states():
    assert estimate(EtaInput(status="done", stage="done", now=0))["percent"] == 100
    assert estimate(EtaInput(status="queued", stage="queued", now=0))["time_left"] == "Waiting to start"
    assert estimate(EtaInput(status="cancelled", stage="cancelled", now=0))["seconds_left"] is None
    r = estimate(EtaInput(status="running", stage="snappyHexMesh", now=5.0, started=0.0, stage_started=2.0))
    assert 0 < r["percent"] < 20 and r["seconds_left"] > 60 and r["time_left"].startswith("about")


@pytest.mark.parametrize("s, text", [
    (None, ""), (5, "less than a minute left"), (59, "less than a minute left"), (60, "about 1 minute left"),
    (61, "about 2 minutes left"), (600, "about 10 minutes left"), (5400, "about 1.5 hours left"),
    (7200, "about 2 hours left"),
])
def test_time_left_text(s, text):
    assert time_left_text(s) == text
