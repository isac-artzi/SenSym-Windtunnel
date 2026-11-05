"""Run a generated case through the OpenFOAM pipeline.

Each stage is an OpenFOAM executable started directly (no shell, never a script from
the case folder) with the OpenFOAM environment captured once from its bashrc.
For every stage we record wall-clock time and the peak resident memory of the whole
process tree (all MPI ranks summed), sampled from /proc.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from windlab import meshcache, results

FOAM_BASHRC = os.environ.get("WINDLAB_FOAM_BASHRC", "/usr/lib/openfoam/openfoam2606/etc/bashrc")
STAGE_TIMEOUT_S = 4 * 3600  # SEC-7: hard wall-clock cap per stage

ProgressFn = Callable[[str, dict], None]


class RunError(RuntimeError):
    def __init__(self, stage: str, message: str, log: Path | None = None):
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.log = log


class RunCancelled(RunError):
    """The run was stopped on request (``cancel`` event set); ``stage`` is where it stopped."""

    def __init__(self, stage: str):
        super().__init__(stage, "cancelled")


TERMINATE_GRACE_S = 10.0  # SIGTERM first (mpirun forwards it to the ranks), then SIGKILL


def terminate_group(proc: subprocess.Popen, grace: float = TERMINATE_GRACE_S) -> None:
    """Stop a stage and every process it started.

    The stage runs in its own session, but OpenMPI puts each rank in a process group of
    its own, so the whole process tree is recorded first. SIGTERM goes to the stage's
    group (mpirun forwards it to the ranks); whatever is still alive after ``grace``
    seconds - in the group or in the recorded tree - gets SIGKILL.
    """
    tree = set(descendants(proc.pid))
    deadline = time.monotonic() + grace
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    while time.monotonic() < deadline:
        tree |= set(descendants(proc.pid))  # until mpirun exits, its children can still be listed
        if proc.poll() is not None and not _alive(tree):
            break
        time.sleep(0.1)
    end = time.monotonic() + 10.0
    while time.monotonic() < end:
        live = _alive(tree | {proc.pid})
        if not live and proc.poll() is not None:
            break
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        for pid in live:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.1)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5.0)


def _proc_state(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return None


def _alive(pids) -> list[int]:
    """The pids that still exist and are not zombies."""
    return [p for p in pids if _proc_state(p) not in (None, "Z", "X")]


def descendants(root: int) -> list[int]:
    """``root`` and all its descendants, from /proc."""
    kids = _children_map()
    out, stack = [], [root]
    while stack:
        pid = stack.pop()
        out.append(pid)
        stack.extend(kids.get(pid, []))
    return out


@lru_cache(maxsize=1)
def foam_env() -> dict[str, str]:
    """Environment after sourcing the OpenFOAM bashrc (captured once)."""
    if not Path(FOAM_BASHRC).is_file():
        raise RunError("setup", f"OpenFOAM not found ({FOAM_BASHRC})")
    out = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", 'source "$0" >/dev/null 2>&1; env -0', FOAM_BASHRC],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    env = dict(item.split("=", 1) for item in out.decode().split("\0") if "=" in item)
    env.pop("BASH_FUNC_module%%", None)
    return env


# --------------------------------------------------------------------------- memory


def _children_map() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as fh:
                stat = fh.read()
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        kids.setdefault(ppid, []).append(int(d))
    return kids


def _tree_rss_bytes(root: int) -> int:
    kids = _children_map()
    total, stack, page = 0, [root], os.sysconf("SC_PAGE_SIZE")
    while stack:
        pid = stack.pop()
        stack.extend(kids.get(pid, []))
        try:
            with open(f"/proc/{pid}/statm") as fh:
                total += int(fh.read().split()[1]) * page
        except OSError:
            pass
    return total


class _MemorySampler(threading.Thread):
    def __init__(self, pid: int, interval: float = 0.2):
        super().__init__(daemon=True)
        self.pid, self.interval = pid, interval
        self.peak = 0
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            self.peak = max(self.peak, _tree_rss_bytes(self.pid))
            self._halt.wait(self.interval)

    def stop(self):
        self._halt.set()
        self.join()


# --------------------------------------------------------------------------- stages


@dataclass
class StageResult:
    name: str
    seconds: float
    peak_rss_mb: float
    returncode: int


@dataclass
class RunReport:
    run_id: str
    case_dir: str
    n_procs: int
    stages: list[StageResult] = field(default_factory=list)
    mesh: dict = field(default_factory=dict)
    coefficients: dict = field(default_factory=dict)
    y_plus: dict = field(default_factory=dict)
    residuals: dict = field(default_factory=dict)
    converged: bool = False
    iterations: int = 0
    notes: list[str] = field(default_factory=list)
    points: list[dict] = field(default_factory=list)  # sweep: one entry per angle
    warm_start: dict | None = None  # single run started from an earlier solution: its alpha_deg / airspeed
    label: str = "uncalibrated prototype - not yet validated"

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.stages)

    @property
    def peak_rss_mb(self) -> float:
        return max((s.peak_rss_mb for s in self.stages), default=0.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_seconds"] = self.total_seconds
        d["peak_rss_mb"] = self.peak_rss_mb
        return d


def _run_stage(
    case: Path,
    name: str,
    argv: list[str],
    report: RunReport,
    progress: ProgressFn | None = None,
    monitor: Callable[[], None] | None = None,
    check: bool = True,
    cancel: threading.Event | None = None,
    log_name: str | None = None,
) -> StageResult:
    log = case / f"log.{log_name or name}"
    if cancel is not None and cancel.is_set():
        raise RunCancelled(name)
    if progress:
        progress("stage", {"stage": name})
    t0 = time.monotonic()
    last_monitor = t0
    with log.open("wb") as fh:
        proc = subprocess.Popen(
            argv,
            cwd=case,
            env={**foam_env(), "PWD": str(case)},  # else OpenFOAM warns "PWD is not the cwd()"
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        sampler = _MemorySampler(proc.pid)
        sampler.start()
        try:
            while True:
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        terminate_group(proc)
                        raise RunCancelled(name) from None
                    if time.monotonic() - t0 > STAGE_TIMEOUT_S:
                        terminate_group(proc, grace=2.0)
                        raise RunError(name, "timed out", log) from None
                    if monitor and time.monotonic() - last_monitor >= 2.0:
                        last_monitor = time.monotonic()
                        monitor()
        except BaseException:
            if proc.poll() is None:  # never leave solver processes behind
                terminate_group(proc, grace=2.0)
            raise
        finally:
            sampler.stop()
    res = StageResult(name, time.monotonic() - t0, sampler.peak / 2**20, proc.returncode)
    report.stages.append(res)
    if progress:
        progress("stage_done", asdict(res))
    if check and proc.returncode != 0:
        tail = log.read_text(errors="replace").splitlines()[-25:]
        raise RunError(name, f"exited with code {proc.returncode}\n" + "\n".join(tail), log)
    return res


def _mpi(n: int, app: str, *args: str) -> list[str]:
    if n == 1:
        return [app, *args]
    return ["mpirun", "-np", str(n), "--bind-to", "none", app, "-parallel", *args]


class ConvergenceMonitor:
    """SIM-10 style stop: trailing-window means of CL and CD stop moving.

    Every call reads the force-coefficient file. Once the running mean over the last
    ``window`` iterations has changed by less than ``cl_tol`` (relative, with an absolute
    floor for CL near zero) and ``cd_tol`` over the last ``window // 2`` iterations, and
    all initial residuals are below ``residual_tol``, it switches ``stopAt`` to
    ``writeNow`` so OpenFOAM writes the latest state and exits.

    ``lag`` (default ``window // 2``) is how far back the earlier mean is taken.
    For a symmetric body at zero incidence set ``cl_abs_max``: CL is then only required
    to average below that magnitude (it wanders around zero and never "settles" in the
    relative sense), CD alone is judged on settling, and ``residual_tol=None`` skips the
    residual test (the cross-flow residuals plateau around 1e-2 in that state).

    The settling of CL and CD is the primary criterion; the residuals are a guard. The
    guard looks only at ``guard_fields`` (solverInfo names such as ``p``, ``Ux``): OpenFOAM
    normalises each initial residual by the size of its own field, so a velocity
    component that is nearly zero everywhere (the spanwise velocity of a wing, or the
    vertical one at zero incidence) has a residual that never drops, however converged
    the flow is. :func:`windlab.case_builder.residual_guard_fields` picks the components
    that carry freestream. ``None`` (the default) guards on every field, as the strict
    2D validation rule does.
    """

    def __init__(self, case: Path, window: int = 200, cl_tol: float = 0.005, cd_tol: float = 0.01,
                 cl_floor: float = 0.002, residual_tol: float | None = 1e-4, min_iterations: int = 0,
                 lag: int | None = None, cl_abs_max: float | None = None, guard_fields: list[str] | None = None,
                 fallback: dict | None = None, progress: ProgressFn | None = None):
        self.case, self.window = case, int(window)
        self.lag = int(lag) if lag else self.window // 2
        self.cl_tol, self.cd_tol, self.cl_floor = cl_tol, cd_tol, cl_floor
        self.cl_abs_max = cl_abs_max
        self.residual_tol = residual_tol
        self.guard_fields = [str(f) for f in guard_fields] if guard_fields else None
        self.min_iterations = min_iterations
        self.progress = progress
        self.stopped_at: int | None = None
        self.fired_window: int = self.window  # the window of the rule that fired (the fallback's if it did)
        self._last_report = 0.0
        # A second, longer-window rule tried alongside the first (task 010): a slowly
        # oscillating case may satisfy means over 200 iterations before means over 100 do.
        self.fallback = None
        if fallback:
            opts = {k: v for k, v in dict(fallback).items() if k in ("window", "lag", "cl_tol", "cd_tol", "cl_floor")}
            self.fallback = ConvergenceMonitor(case, residual_tol=None, cl_abs_max=cl_abs_max, **opts)

    def check(self) -> None:
        coeffs = results.read_force_coeffs(self.case)
        if coeffs is None or len(coeffs["Time"]) < 2:
            return
        it, cl, cd = coeffs["Time"], coeffs["Cl"], coeffs["Cd"]
        if self.progress and time.monotonic() - self._last_report > 10:
            self._last_report = time.monotonic()
            self.progress("iteration", {"iteration": int(it[-1]), "Cl": float(cl[-1]), "Cd": float(cd[-1])})
        if self.stopped_at is not None or len(it) < max(self.min_iterations, self.window + self.lag):
            return
        if self.settled(cl, cd):
            self.stop(int(it[-1]))

    def settled(self, cl, cd) -> bool:
        """The stop rule on CL / CD histories (numpy arrays) plus the current residuals."""
        if self._means_agree(cl, cd):
            self.fired_window = self.window
        elif self.fallback is not None and self.fallback._means_agree(cl, cd):
            self.fired_window = self.fallback.window
        else:
            return False
        return self.residuals_ok()

    def _means_agree(self, cl, cd) -> bool:
        w, lag = self.window, self.lag
        if len(cl) < w + lag:
            return False
        cl_now, cl_then = cl[-w:].mean(), cl[-w - lag : -lag].mean()
        cd_now, cd_then = cd[-w:].mean(), cd[-w - lag : -lag].mean()
        if self.cl_abs_max is not None:
            cl_ok = abs(cl_now) < self.cl_abs_max
        else:
            cl_ok = abs(cl_now - cl_then) <= max(self.cl_tol * abs(cl_now), self.cl_floor)
        cd_ok = abs(cd_now - cd_then) <= self.cd_tol * abs(cd_now)
        return bool(cl_ok and cd_ok)

    def residuals_ok(self) -> bool:
        """The residual guard: every guarded field's latest initial residual is below the tolerance."""
        if self.residual_tol is None:
            return True
        res = results.final_residuals(self.case)
        if self.guard_fields is not None:
            res = {k: v for k, v in res.items() if k in self.guard_fields}
        return bool(res) and max(res.values()) < self.residual_tol

    def stop(self, iteration: int) -> None:
        """Ask OpenFOAM to write the current state and exit (re-read via runTimeModifiable)."""
        ctrl = self.case / "system" / "controlDict"
        text = ctrl.read_text()
        ctrl.write_text(re.sub(r"^stopAt\s+\w+;", "stopAt          writeNow;", text, flags=re.M))
        self.stopped_at = iteration
        if self.progress:
            self.progress("converged", {"iteration": self.stopped_at})


_MONITOR_OPTIONS = {"window", "cl_tol", "cd_tol", "cl_floor", "residual_tol", "min_iterations", "lag", "cl_abs_max",
                    "guard_fields", "fallback"}
_STAGED_FILES = {"system/fvSchemes", "system/fvSolution"}


class StartupSwitch:
    """Swap robust start-up solver settings for the final ones after N iterations.

    The case ships ``system/<dict>`` (start-up) and ``system/<dict>.final``; once the
    force-coefficient file reaches ``iterations``, each ``.final`` file is copied over
    its dictionary and OpenFOAM re-reads it (``runTimeModifiable``). Only the files in
    ``_STAGED_FILES`` can be swapped, and both versions were rendered and checked by the
    case builder.
    """

    def __init__(self, case: Path, iterations: int, files: list[str], progress: ProgressFn | None = None):
        self.case, self.iterations, self.progress = case, int(iterations), progress
        self.files = [f for f in files if f in _STAGED_FILES]
        self.switched_at: int | None = None

    def check(self) -> None:
        if self.switched_at is not None:
            return
        coeffs = results.read_force_coeffs(self.case)
        if coeffs is None or len(coeffs["Time"]) == 0 or coeffs["Time"][-1] < self.iterations:
            return
        for f in self.files:
            src, dst = self.case / f"{f}.final", self.case / f
            tmp = dst.with_name(dst.name + ".tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)  # atomic, so OpenFOAM never reads half a file
        self.switched_at = int(coeffs["Time"][-1])
        if self.progress:
            self.progress("startup_done", {"iteration": self.switched_at})


def _is_time_name(name: str) -> bool:
    try:
        float(name)
        return True
    except ValueError:
        return False


def _copy_initial_fields(case: Path, n: int) -> None:
    targets = sorted(case.glob("processor*")) if n > 1 else [case]
    for t in targets:
        shutil.rmtree(t / "0", ignore_errors=True)
        shutil.copytree(case / "0.orig", t / "0")


def clear_solution(case: Path) -> None:
    """Remove a previous solve: time folders other than 0 (root and processor*) and postProcessing."""
    for t in [case, *sorted(case.glob("processor*"))]:
        for d in t.iterdir():
            if d.is_dir() and not d.is_symlink() and _is_time_name(d.name) and float(d.name) != 0:
                shutil.rmtree(d)
    shutil.rmtree(case / "postProcessing", ignore_errors=True)


def _atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp, follow_symlinks=False)
    os.replace(tmp, dst)


def _warm_from_latest(case: Path, n: int) -> bool:
    """Make the latest written solution the new start time 0 (sweeps: the previous angle).

    The fresh 0.orig fields (this angle's boundary conditions) are copied first and the
    solved fields (U, p, k, omega, nut, phi) are laid over them; every other time folder
    goes. Returns False, leaving the case untouched, when any rank has no solved fields -
    the caller then starts from scratch."""
    ranks = meshcache.rank_dirs(case, n)
    latest = [meshcache.latest_time_dir(r) for r in ranks]
    if any(t is None or float(t.name) <= 0 or not all((t / f).is_file() for f in meshcache.FIELD_FILES)
           for t in latest):
        return False
    for r, t in zip(ranks, latest, strict=True):
        new = r / "0.warm"
        shutil.rmtree(new, ignore_errors=True)
        shutil.copytree(case / "0.orig", new)
        for f in meshcache.FIELD_FILES:
            shutil.copyfile(t / f, new / f)
        for d in r.iterdir():
            if d.is_dir() and not d.is_symlink() and _is_time_name(d.name):
                shutil.rmtree(d)
        os.rename(new, r / "0")
    return True


def _mesh_snappy(case: Path, n: int, report: RunReport, progress: ProgressFn | None,
                 cancel: threading.Event | None, cache: meshcache.MeshCache | None = None,
                 key: str | None = None) -> None:
    """external_aero: background block mesh + snappyHexMesh with boundary layers - or, with
    a cache hit, the mesh of an earlier run on the same shape copied in (stage "meshReuse")."""
    if cache is not None and key is not None and cache.lookup(key) is not None:
        if cancel is not None and cancel.is_set():
            raise RunCancelled("meshReuse")
        if progress:
            progress("stage", {"stage": "meshReuse"})
        t0 = time.monotonic()
        try:
            report.mesh = cache.install_mesh(key, case, n)
        except (KeyError, OSError) as e:  # a damaged entry: mesh from scratch instead
            report.notes.append(f"cached mesh unusable ({e}); meshed again")
        else:
            _copy_initial_fields(case, n)
            report.mesh["reused"] = True
            res = StageResult("meshReuse", time.monotonic() - t0, 0.0, 0)
            report.stages.append(res)
            if progress:
                progress("stage_done", asdict(res))
                progress("mesh_reused", {"cells": report.mesh.get("cells")})
            return
    _run_stage(case, "blockMesh", ["blockMesh"], report, progress, cancel=cancel)
    _run_stage(case, "surfaceFeatureExtract", ["surfaceFeatureExtract"], report, progress, cancel=cancel)
    if n > 1:
        _run_stage(case, "decomposePar", ["decomposePar", "-force"], report, progress, cancel=cancel)
    _run_stage(case, "snappyHexMesh", _mpi(n, "snappyHexMesh", "-overwrite"), report, progress, cancel=cancel)
    # Initial fields go in after meshing so they see the new body patches.
    _copy_initial_fields(case, n)
    _run_stage(case, "checkMesh", _mpi(n, "checkMesh", "-constant"), report, progress, check=False, cancel=cancel)
    report.mesh = results.parse_check_mesh(case / "log.checkMesh")
    report.mesh.update(results.parse_snappy_layers(case / "log.snappyHexMesh"))
    if cache is not None and key is not None and report.mesh.get("cells"):
        try:
            cache.store_mesh(key, case, n, report.mesh)
        except OSError as e:  # the cache is a convenience, never a reason to fail
            report.notes.append(f"mesh not cached ({e})")


def _mesh_block_2d(case: Path, n: int, report: RunReport, progress: ProgressFn | None,
                   cancel: threading.Event | None) -> None:
    """airfoil_2d: the whole structured mesh comes from blockMesh."""
    _run_stage(case, "blockMesh", ["blockMesh"], report, progress, cancel=cancel)
    _run_stage(case, "checkMesh", ["checkMesh", "-constant"], report, progress, check=False, cancel=cancel)
    report.mesh = results.parse_check_mesh(case / "log.checkMesh")
    _copy_initial_fields(case, 1)
    if n > 1:
        _run_stage(case, "decomposePar", ["decomposePar", "-force"], report, progress, cancel=cancel)


def _solve(case: Path, n: int, meta: dict, report: RunReport, progress: ProgressFn | None,
           cancel: threading.Event | None, suffix: str = "", warm: dict | None = None) -> dict:
    """potentialFoam + simpleFoam with the stop rule; returns this solve's numbers.

    With ``warm`` (the stored meta of an earlier solution whose fields are already in the
    start-time folders) the potential-flow start is replaced by ``changeDictionary``, which
    sets this run's far-field values on the inherited fields."""
    if warm is not None:
        _run_stage(case, "changeDictionary", _mpi(n, "changeDictionary", "-time", "0"), report, progress,
                   cancel=cancel, log_name=f"changeDictionary{suffix}")
        if progress:
            progress("warm_start", {k: warm.get(k) for k in ("alpha_deg", "airspeed", "iterations")})
    else:
        _run_stage(case, "potentialFoam", _mpi(n, "potentialFoam", "-writephi"), report, progress, cancel=cancel,
                   log_name=f"potentialFoam{suffix}")

    conv = {k: v for k, v in meta.get("convergence", {}).items() if k in _MONITOR_OPTIONS}
    mon = ConvergenceMonitor(case, progress=progress, **conv)
    checks = [mon.check]
    startup = meta.get("startup")
    if startup:
        switch = StartupSwitch(case, startup["iterations"], startup["files"], progress)
        checks.insert(0, switch.check)
        # never judge convergence on start-up iterations
        mon.min_iterations = max(mon.min_iterations, int(startup["iterations"]) + mon.window + mon.lag)

    def monitor() -> None:
        for check in checks:
            check()

    _run_stage(case, "simpleFoam", _mpi(n, "simpleFoam"), report, progress, monitor=monitor, cancel=cancel,
               log_name=f"simpleFoam{suffix}")

    coeffs = results.read_force_coeffs(case)
    if coeffs is None:
        raise RunError("simpleFoam", "no force coefficients were written", case / f"log.simpleFoam{suffix}")
    # Settled runs report the mean over the stop rule's window (what the rule judged);
    # runs that hit the cap (unsteady wakes) keep the longer 200-iteration average.
    window = mon.fired_window if mon.stopped_at is not None else 200
    out = {
        "iterations": int(coeffs["Time"][-1]),
        "converged": mon.stopped_at is not None,
        "coefficients": results.summarize_coeffs(coeffs, window=window),
        "y_plus": results.read_y_plus(case),
        "residuals": results.final_residuals(case),
        "warm_start": None if warm is None else {k: warm.get(k) for k in ("alpha_deg", "airspeed")},
        "notes": [],
    }
    forces = results.read_forces(case)
    if forces is not None and all(k in meta for k in ("drag_dir", "lift_dir", "rho", "airspeed", "a_ref")):
        out["coefficients"].update(results.force_split(forces, meta["drag_dir"], meta["lift_dir"], meta["rho"],
                                                       meta["airspeed"], meta["a_ref"], window=window))
    if not out["converged"]:
        out["notes"].append(f"CL/CD did not settle within {meta['max_iterations']} iterations - treat as indicative")
    if out["coefficients"].get("cl_oscillation", 0) > 0.02:
        out["notes"].append("CL oscillates by more than 2 % - likely unsteady, treat as indicative")
    return out


PointHook = Callable[[int, dict], None]


def point_dir(case: Path, index: int) -> Path:
    """Inputs (and afterwards the postProcessing) of one sweep angle."""
    return Path(case) / "sweep" / f"p{int(index)}"


def _prepare_point(case: Path, n: int, index: int, warm: bool = False) -> bool:
    """Install one sweep angle: its controlDict, inlet velocity and far-field patch. With
    ``warm`` the previous angle's solution becomes the start (returns True when it could)."""
    src = point_dir(case, index)
    _atomic_copy(src / "controlDict", case / "system" / "controlDict")
    _atomic_copy(src / "U", case / "0.orig" / "U")
    if (src / "changeDictionaryDict").is_file():
        _atomic_copy(src / "changeDictionaryDict", case / "system" / "changeDictionaryDict")
    if warm and _warm_from_latest(case, n):
        shutil.rmtree(case / "postProcessing", ignore_errors=True)
        return True
    clear_solution(case)
    _copy_initial_fields(case, n)
    return False


def _archive_point(case: Path, index: int) -> None:
    """Keep this angle's postProcessing next to its inputs (sweep/p<i>/postProcessing)."""
    src, dst = case / "postProcessing", point_dir(case, index) / "postProcessing"
    if src.is_dir():
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(src), str(dst))


def _cached_warm_start(cache: meshcache.MeshCache | None, key: str | None, case: Path, n: int,
                       meta: dict) -> dict | None:
    """Install the nearest stored solution (same airspeed, nearest angle) as the start fields."""
    if cache is None or key is None or meta.get("alpha_deg") is None:
        return None
    found = cache.nearest_solution(key, float(meta["alpha_deg"]), float(meta["airspeed"]))
    if found is None:
        return None
    sol, sol_meta = found
    try:
        cache.install_solution(sol, case, n)
    except (KeyError, OSError, ValueError):
        _copy_initial_fields(case, n)  # back to a clean start
        return None
    return sol_meta


def _remember_solution(cache: meshcache.MeshCache | None, key: str | None, case: Path, n: int, meta: dict,
                       res: dict) -> None:
    if cache is None or key is None or res.get("status", "done") != "done":
        return
    c = res.get("coefficients", {})
    with contextlib.suppress(OSError, ValueError):
        cache.store_solution(key, case, n, {"alpha_deg": meta.get("alpha_deg"), "airspeed": meta.get("airspeed"),
                                            "iterations": res.get("iterations"), "converged": res.get("converged"),
                                            "cl": c.get("cl"), "cd": c.get("cd")})


def run_case(case_dir: Path, progress: ProgressFn | None = None, mesh_only: bool = False,
             cancel: threading.Event | None = None, on_point: PointHook | None = None,
             cache: meshcache.MeshCache | None = None) -> RunReport:
    """Mesh and solve a case written by :func:`windlab.case_builder.build_case`.

    A sweep case (``sweep`` in windlab_case.json) is meshed once and then solved once per
    angle by swapping in that angle's controlDict and inlet velocity (no remeshing); from
    the second angle on, each angle starts from the previous angle's solution. An
    angle that fails is recorded and the sweep goes on; ``cancel`` stops everything.
    For sweeps, ``on_point(i, result)`` runs after each angle while its postProcessing is
    still in place (the web app makes its surface and slice pictures there); a single
    run's postProcessing simply stays in the case folder.

    With a :class:`windlab.meshcache.MeshCache`, the mesh of an earlier run on the same
    shape (and preset, orientation, rank count) is reused instead of meshed again, a single
    run starts from the stored solution at the nearest angle of the same airspeed, and the
    finished solution is stored for the next run.
    """
    case = Path(case_dir)
    meta = json.loads((case / "windlab_case.json").read_text())
    n = int(meta["n_procs"])
    report = RunReport(run_id=meta["run_id"], case_dir=str(case), n_procs=n)

    template = meta.get("template", "external_aero")
    key = None
    if template == "external_aero":
        key = meshcache.mesh_key(case) if cache is not None else None
        _mesh_snappy(case, n, report, progress, cancel, cache=cache, key=key)
    elif template == "airfoil_2d":
        _mesh_block_2d(case, n, report, progress, cancel)
    else:
        raise RunError("setup", f"unknown case template {template!r}")
    if progress:
        progress("mesh", report.mesh)
    if mesh_only:
        (case / "windlab_report.json").write_text(json.dumps(report.to_dict(), indent=2))
        return report

    points = (meta.get("sweep") or {}).get("points")
    if not points:
        warm = _cached_warm_start(cache, key, case, n, meta)
        res = _solve(case, n, meta, report, progress, cancel, warm=warm)
        report.iterations, report.converged = res["iterations"], res["converged"]
        report.coefficients, report.y_plus, report.residuals = res["coefficients"], res["y_plus"], res["residuals"]
        report.warm_start = res["warm_start"]
        report.notes += res["notes"]
        _remember_solution(cache, key, case, n, meta, res)
        (case / "windlab_report.json").write_text(json.dumps(report.to_dict(), indent=2))
        return report

    previous: dict | None = None  # the previous angle's result, the warm start for the next
    for i, pt in enumerate(points):
        if cancel is not None and cancel.is_set():
            raise RunCancelled("simpleFoam")
        if progress:
            progress("point", {"index": i, "alpha_deg": pt["alpha_deg"], "count": len(points)})
        warm = None
        if _prepare_point(case, n, i, warm=previous is not None):
            warm = {"alpha_deg": previous["alpha_deg"], "airspeed": meta["airspeed"],
                    "iterations": previous.get("iterations")}
        elif i == 0:
            warm = _cached_warm_start(cache, key, case, n, {**meta, **pt})
        t0 = time.monotonic()
        try:
            # this angle's own freestream, force directions and stop-rule guard fields
            res = _solve(case, n, {**meta, **pt}, report, progress, cancel, suffix=f".{i}", warm=warm)
            res["status"] = "done"
        except RunCancelled:
            raise
        except RunError as e:
            res = {"status": "failed", "stage": e.stage, "error": str(e).splitlines()[0][:300]}
        res["alpha_deg"] = pt["alpha_deg"]
        res["seconds"] = time.monotonic() - t0
        previous = res if res["status"] == "done" else None
        _remember_solution(cache, key, case, n, {**meta, **pt}, res)
        if on_point:
            on_point(i, res)
        _archive_point(case, i)
        report.points.append(res)
        if progress:
            progress("point_done", {"index": i, "status": res["status"]})
    report.iterations = sum(p.get("iterations", 0) for p in report.points)
    report.converged = all(p.get("converged") for p in report.points)
    (case / "windlab_report.json").write_text(json.dumps(report.to_dict(), indent=2))
    return report
