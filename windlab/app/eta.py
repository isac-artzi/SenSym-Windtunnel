"""Time-left estimate for a run ("about 4 min left").

Model
-----
* Every stage has a *prior* duration: the Quick NACA run measured in the dev container
  (4 ranks: snappyHexMesh 11 s, simpleFoam 66 s for ~400 iterations), multiplied by
  ``PRIOR_SAFETY`` (student laptops under WSL are expected to be slower) and scaled for
  the number of cores.
* Once stages have finished, their measured/prior ratio gives a *machine speed factor*
  that rescales the priors of the stages still to come.
* While the solver runs, the time left is (iterations still expected) / (iteration
  rate). The expected total is at least ``EXPECTED_ITERATIONS`` (the stop rule needs
  >= 300; Quick runs settle at 330-400), grows with the iteration count once that is
  passed, and never exceeds the iteration cap. Until ``MIN_ITERATIONS_FOR_RATE``
  iterations exist, the slower of the measured and the prior rate is used, so early
  estimates err on the long side.
* A sweep adds, for each angle still to come, a potentialFoam start plus the expected
  iterations (the larger of the prior and the mean of finished angles) at the rate.

All inputs are explicit so the estimator is a pure function (unit-tested).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

PRIOR_SAFETY = 2.0
REFERENCE_PROCS = 4
# Seconds per stage on the reference machine with 4 ranks (Quick preset, NACA 0012 wing).
PRIOR_SECONDS = {
    "blockMesh": 1.0,
    "surfaceFeatureExtract": 1.0,
    "decomposePar": 1.0,
    "snappyHexMesh": 11.0,
    "meshReuse": 2.0,  # the whole meshing replaced by a copy from the cache
    "checkMesh": 2.0,
    "potentialFoam": 2.0,
    "changeDictionary": 2.0,  # warm start: replaces potentialFoam
    "simpleFoam": 66.0,
    "surface": 2.0,
}
MESH_STAGES = ("blockMesh", "surfaceFeatureExtract", "decomposePar", "snappyHexMesh", "checkMesh")
START_STAGES = ("potentialFoam", "changeDictionary")
PRIOR_ITERATIONS = 400  # iterations behind PRIOR_SECONDS["simpleFoam"]
EXPECTED_ITERATIONS = 500  # conservative expected count before the stop rule fires
MIN_ITERATIONS_FOR_RATE = 150
MIN_SECONDS_FOR_RATE = 10.0


@dataclass
class EtaInput:
    status: str  # queued | running | done | failed | cancelled
    stage: str
    now: float
    started: float | None = None
    stage_started: float | None = None
    stage_times: dict = field(default_factory=dict)  # finished stage -> seconds (summed over angles)
    iteration: int = 0  # current solver iteration (of the current angle)
    converged: bool = False  # the stop rule has fired for the current angle
    max_iterations: int = 1500
    n_procs: int = 4
    n_points: int = 1  # angles to solve (1 for a single run)
    point_index: int = 0  # angle being solved now (0-based)
    finished_point_iterations: list = field(default_factory=list)
    finished_point_seconds: list = field(default_factory=list)


def _core_scale(n_procs: int) -> float:
    """Imperfect parallel speed-up: half again as long on 2 cores, not twice."""
    return (REFERENCE_PROCS / max(1, n_procs)) ** 0.8


def prior(stage: str, n_procs: int) -> float:
    return PRIOR_SECONDS.get(stage, 1.0) * PRIOR_SAFETY * _core_scale(n_procs)


def speed_factor(stage_times: dict, n_procs: int) -> float:
    """Measured / prior time of the finished stages (1 = as the prior; >1 = slower).

    Uses only stages whose priors add up to a few seconds, so a fast blockMesh alone
    doesn't make the estimate optimistic."""
    done = {k: v for k, v in stage_times.items() if k in PRIOR_SECONDS and k not in ("simpleFoam", "surface")}
    p = sum(prior(k, n_procs) for k in done)
    if p < prior("snappyHexMesh", n_procs) * 0.9:
        return 1.0
    f = sum(done.values()) / p
    # trust a fast machine only part of the way (the solver scales worse than meshing)
    return min(10.0, max(0.3, f if f >= 1 else math.sqrt(f)))


def expected_iterations(iteration: int, cap: int, converged: bool = False) -> int:
    if converged:
        return max(iteration, 1)
    exp = max(EXPECTED_ITERATIONS, iteration + max(100, int(0.25 * iteration)))
    return int(min(cap, exp))


def iteration_rate(inp: EtaInput, speed: float) -> float:
    """Iterations per second for the solver: measured, held to the prior early on."""
    prior_rate = PRIOR_ITERATIONS / (prior("simpleFoam", inp.n_procs) * speed)
    if inp.stage != "simpleFoam" or inp.stage_started is None:
        return prior_rate
    elapsed = inp.now - inp.stage_started
    if inp.iteration <= 0 or elapsed <= 0:
        return prior_rate
    measured = inp.iteration / elapsed  # includes start-up: slightly pessimistic
    if inp.iteration < MIN_ITERATIONS_FOR_RATE or elapsed < MIN_SECONDS_FOR_RATE:
        return min(measured, prior_rate)
    return measured


def _remaining_in_stage(stage: str, inp: EtaInput, speed: float) -> float:
    p = prior(stage, inp.n_procs) * speed
    elapsed = max(0.0, inp.now - (inp.stage_started if inp.stage_started is not None else inp.now))
    return max(p - elapsed, 0.2 * p)  # an overrunning stage still has a little left


def seconds_left(inp: EtaInput) -> float | None:
    if inp.status != "running":
        return 0.0 if inp.status == "done" else None
    speed = speed_factor(inp.stage_times, inp.n_procs)
    rate = iteration_rate(inp, speed)
    stages_after_mesh = ("potentialFoam", "simpleFoam", "surface")
    left = 0.0

    mesh = [s for s in MESH_STAGES if s != "decomposePar" or inp.n_procs > 1]
    if inp.stage == "meshReuse":  # a cached mesh: nothing else of the meshing is left
        left += _remaining_in_stage("meshReuse", inp, speed)
        current = None
        angles_to_come = inp.n_points
    elif inp.stage in mesh or inp.stage in ("queued", "setup"):
        i = mesh.index(inp.stage) if inp.stage in mesh else -1
        if i >= 0:
            left += _remaining_in_stage(inp.stage, inp, speed)
        left += sum(prior(s, inp.n_procs) * speed for s in mesh[i + 1 :])
        current = None  # the solver hasn't started: every angle is still to come
        angles_to_come = inp.n_points
    else:
        current = inp.stage
        angles_to_come = max(0, inp.n_points - inp.point_index - 1)

    # the angle being solved now
    if current in START_STAGES:
        left += _remaining_in_stage(current, inp, speed)
        left += expected_iterations(0, inp.max_iterations) / rate
        left += prior("surface", inp.n_procs) * speed
    elif current == "simpleFoam":
        left += max(0, expected_iterations(inp.iteration, inp.max_iterations, inp.converged) - inp.iteration) / rate
        left += prior("surface", inp.n_procs) * speed
    elif current == "surface":
        left += _remaining_in_stage("surface", inp, speed)

    # angles still to come (sweeps)
    if angles_to_come:
        done_its = [i for i in inp.finished_point_iterations if i]
        per_angle_its = max(EXPECTED_ITERATIONS, sum(done_its) / len(done_its)) if done_its else EXPECTED_ITERATIONS
        per_angle_its = min(per_angle_its, inp.max_iterations)
        per_angle = sum(prior(s, inp.n_procs) * speed for s in stages_after_mesh if s != "simpleFoam")
        per_angle += per_angle_its / rate
        if inp.finished_point_seconds:  # measured whole-angle times are the best guide
            per_angle = max(per_angle, sum(inp.finished_point_seconds) / len(inp.finished_point_seconds))
        left += angles_to_come * per_angle
    return left


def estimate(inp: EtaInput) -> dict:
    """``percent`` done, ``seconds_left`` and a plain-language ``time_left`` text."""
    if inp.status == "done":
        return {"percent": 100.0, "seconds_left": 0.0, "time_left": "Finished"}
    if inp.status == "queued":
        return {"percent": 0.0, "seconds_left": None, "time_left": "Waiting to start"}
    if inp.status != "running":
        return {"percent": 0.0, "seconds_left": None, "time_left": ""}
    left = seconds_left(inp) or 0.0
    elapsed = max(0.0, inp.now - (inp.started if inp.started is not None else inp.now))
    percent = 100.0 * elapsed / (elapsed + left) if elapsed + left > 0 else 0.0
    return {"percent": round(min(99.0, percent), 1), "seconds_left": round(left), "time_left": time_left_text(left)}


def time_left_text(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return "less than a minute left"
    m = math.ceil(seconds / 60)  # round up: better to finish early than late
    if m >= 90:
        h = m / 60
        return f"about {h:.1f} hours left".replace(".0 hours", " hours")
    return f"about {m} minute{'s' if m != 1 else ''} left"
