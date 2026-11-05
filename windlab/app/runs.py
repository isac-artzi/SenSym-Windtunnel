"""Run queue: one simulation at a time in a background thread.

``POST /api/runs`` builds the case folder (fast, all numbers clamped and checked by
:mod:`windlab.case_builder`) and puts the run id on a queue; a single worker thread
takes runs off the queue and drives the OpenFOAM stages as subprocesses
(:func:`windlab.runner.run_case`). Requests never wait for a run.

Status lives in memory while the server runs and in ``windlab_status.json`` in the case
folder afterwards, so results survive a restart. Live CL/CD history is read straight
from the solver's force-coefficient file when asked for.
"""

from __future__ import annotations

import csv
import errno
import inspect
import io
import json
import logging
import queue
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from windlab import disclaimer, expected_error, machine, meshcache, results, speed
from windlab.app import eta, housekeeping, surface
from windlab.app import slice as flow_slice
from windlab.app.geometry_store import GeometryError, GeometryStore, parse_uuid
from windlab.case_builder import (
    MAX_SWEEP_ANGLES,
    N_LAYERS,
    NU_SEA_LEVEL,
    PRESETS,
    RHO_SEA_LEVEL,
    SURFACE_LEVELS,
    FlowConditions,
    GeometryInput,
    InputError,
    Preset,
    ReferenceValues,
    build_case,
    build_sweep_case,
    reynolds_number,
    safe_join,
    sweep_angles,
)
from windlab.feedback import UsageLog
from windlab.geometry import thin
from windlab.geometry.upload import _fmt_length
from windlab.runner import RunCancelled, RunError, point_dir, run_case

log = logging.getLogger("windlab.runs")

LABEL = "Uncalibrated prototype — not yet validated"
# Quick is the everyday mesh; Standard is offered as "more accurate, slower"; Detailed only
# on a big, fast computer (windlab.speed.available_presets decides per machine).
EXPOSED_PRESETS = {Preset.QUICK, Preset.STANDARD}
PRESET_LABELS = {
    Preset.QUICK: ("Quick", "about 0.4 million cells; the everyday choice"),
    Preset.STANDARD: ("More accurate, slower", "about twice the cells of Quick; lift changes by about 1 %, "
                                               "drag by a few per cent; needs about 2 GB of memory"),
    Preset.DETAILED: ("Detailed", "about five times the cells of Quick; needs about 7 GB of memory"),
}
HISTORY_POINTS = 400

# Plain-language stage names, and each stage's share of a typical Quick run (wall clock
# on the dev container: snappyHexMesh ~15 %, simpleFoam ~80 %). The time-left estimate
# itself lives in windlab.app.eta.
STAGES: dict[str, tuple[str, float]] = {
    "queued": ("Waiting for the previous run to finish", 0.0),
    "blockMesh": ("Building the wind-tunnel box", 0.01),
    "surfaceFeatureExtract": ("Finding the sharp edges of your part", 0.01),
    "decomposePar": ("Splitting the work between processor cores", 0.01),
    "snappyHexMesh": ("Fitting the mesh around your part", 0.15),
    "meshReuse": ("Reusing the mesh from your earlier run of this shape", 0.01),
    "checkMesh": ("Checking the mesh quality", 0.01),
    "potentialFoam": ("Making a first guess of the airflow", 0.01),
    "changeDictionary": ("Starting from your earlier result", 0.01),
    "simpleFoam": ("Solving the airflow", 0.79),
    "surface": ("Preparing the 3D pressure picture", 0.01),
    "done": ("Finished", 0.0),
    "failed": ("Stopped with a problem", 0.0),
    "cancelled": ("Stopped (you cancelled this run)", 0.0),
}


class RunRequestError(ValueError):
    """Plain-language problem with a run request (HTTP 400/404)."""


class RunConflictError(RunRequestError):
    """The run is in the wrong state for this request (HTTP 409)."""


ACTIVE = frozenset({"queued", "running"})
MAX_LISTED_RUNS = 200


@dataclass
class RunState:
    run_id: str
    case_dir: Path
    geometry_id: str
    params: dict
    status: str = "queued"  # queued | running | done | failed | cancelled
    stage: str = "queued"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    stage_started: float | None = None
    stage_times: dict = field(default_factory=dict)
    iteration: int = 0
    converged_at: int | None = None
    result: dict | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    points: list = field(default_factory=list)  # sweep: one dict per angle
    point_index: int = 0  # sweep: the angle being solved
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    def to_json(self) -> dict:
        return {
            "points": self.points,
            "point_index": self.point_index,
            "run_id": self.run_id,
            "geometry_id": self.geometry_id,
            "params": self.params,
            "status": self.status,
            "stage": self.stage,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "stage_times": self.stage_times,
            "iteration": self.iteration,
            "converged_at": self.converged_at,
            "result": self.result,
            "warnings": self.warnings,
            "error": self.error,
        }


Runner = Callable[..., object]


class RunManager:
    def __init__(self, runs_root: Path, geometry: GeometryStore, n_procs: int = 4, runner: Runner = run_case,
                 data_root: Path | None = None, keep_runs: int = housekeeping.DEFAULT_KEEP_RUNS,
                 disk_cap_bytes: int = housekeeping.DEFAULT_DISK_CAP_BYTES,
                 geometry_max_age_days: float = housekeeping.DEFAULT_GEOMETRY_MAX_AGE_DAYS,
                 incoming: Path | None = None, mesh_cache: bool = True):
        self.root = Path(runs_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_root = Path(data_root) if data_root else self.root.parent
        self.geometry = geometry
        self.n_procs = int(n_procs)
        self.runner = runner
        # mesh + solution reuse between runs of the same shape (windlab.meshcache); handed to
        # runners that take a ``cache`` argument (the real one does, test fakes need not)
        self.cache = meshcache.MeshCache(self.data_root / "mesh-cache") if mesh_cache else None
        self._runner_takes_cache = "cache" in inspect.signature(runner).parameters
        self.keep_runs = max(1, int(keep_runs))
        self.disk_cap_bytes = int(disk_cap_bytes)
        self.geometry_max_age_days = float(geometry_max_age_days)
        self.incoming = incoming
        self.usage = UsageLog(self.data_root)  # anonymous trial statistics (windlab feedback export)
        self._runs: dict[str, RunState] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, name="windlab-runs", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until the queue is empty and nothing is running (for tests)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                busy = any(r.status in ("queued", "running") for r in self._runs.values())
            if not busy:
                return True
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------------ submit

    def submit(self, geometry_id: str, airspeed: float, alpha_deg: float, preset: str = "quick",
               units_confirmed: bool = False) -> RunState:
        gid, meta, geom, reference, preset_v = self._prepare(geometry_id, preset, units_confirmed)
        conditions = _conditions(airspeed, alpha_deg)
        try:
            case = build_case(geom, conditions, reference, preset=preset_v, n_procs=self.n_procs, runs_root=self.root)
        except InputError as e:
            raise RunRequestError(f"This run can't be set up: {e}.") from None
        return self._enqueue(case, gid, meta, "single")

    def submit_sweep(self, geometry_id: str, airspeed: float, angles, preset: str = "quick",
                     units_confirmed: bool = False) -> RunState:
        """Angle-of-attack sweep: one mesh, one solve per angle (2 to 8 angles)."""
        gid, meta, geom, reference, preset_v = self._prepare(geometry_id, preset, units_confirmed)
        _conditions(airspeed, 0.0)  # airspeed check with the plain-language message
        if not isinstance(angles, (list, tuple)):
            raise RunRequestError("Please give the angles of attack as a list.")
        try:
            vals = sweep_angles(angles)
        except InputError as e:
            raise RunRequestError(f"Check the angles: {e}.") from None
        try:
            case = build_sweep_case(geom, airspeed, vals, reference, preset=preset_v, n_procs=self.n_procs,
                                    runs_root=self.root)
        except InputError as e:
            raise RunRequestError(f"This sweep can't be set up: {e}.") from None
        return self._enqueue(case, gid, meta, "sweep")

    def _enqueue(self, case, gid: str, meta: dict, kind: str) -> RunState:
        p = case.params
        ref = meta["reference"]
        params = {
            "kind": kind,
            "airspeed": p["airspeed"],
            "alpha_deg": p["alpha_deg"],
            "angles": p.get("angles"),
            "preset": p["preset"],
            "reynolds": p["reynolds"],
            "mach": p["mach"],
            "reference_area": p["a_ref"],
            "reference_length": p["l_ref"],
            "reference_how": ref.get("how"),
            "part_type": ref.get("part_type"),  # uploads: "wing" (planform area) or "body" (frontal area)
            "area_method": ref.get("area_method"),
            "mirror_symmetric": (meta.get("report") or {}).get("mirror_symmetric"),  # {"x", "y", "z"} or None
            "thin_features": p.get("thin_features"),  # windlab.geometry.thin: thin plates / fins found, or None
            "thin_refinement": p.get("thin_refinement"),  # the refinement band used around them, or None
            "max_iterations": p["max_iterations"],
            "n_procs": p["n_procs"],
            "geometry_name": meta.get("name"),
            "geometry_class": meta.get("class") or ("streamlined-lifting" if meta.get("kind") == "naca" else "unknown"),
        }
        state = RunState(run_id=case.run_id, case_dir=case.path, geometry_id=gid, params=params)
        if kind == "sweep":
            state.points = [{"alpha_deg": a, "status": "waiting"} for a in p["angles"]]
        with self._lock:
            self._runs[state.run_id] = state
            self._order.append(state.run_id)
        self._save(state)
        self.geometry.touch(gid)
        self._queue.put(state.run_id)
        self.usage.event(kind if kind == "sweep" else "run")
        self.start()
        return state

    # ------------------------------------------------------------------ lesson packs

    def import_pack(self, lesson_id: str, name: str, step: dict) -> RunState:
        """Bring a lesson's pre-computed result into "My runs" as a finished run (EDU-1).

        The shape is regenerated from the lesson's run step (``shape``), the case folder is
        built from WindLab's template with the step's ``conditions`` (so the OpenFOAM case
        export works), and the pack's result, pictures and force histories are installed.
        A pack imported before is simply returned again."""
        from windlab import lessons

        key = f"{lessons.parse_id(lesson_id)}/{name}"
        existing = self._find_pack_run(key)
        if existing is not None:
            return existing
        pack = lessons.load_pack(lesson_id, name)  # KeyError / LessonError if missing
        shape, cond = step.get("shape") or {}, step.get("conditions") or {}
        try:
            if "naca" in shape:
                n = shape["naca"]
                meta = self.geometry.add_naca(str(n["code"]), float(n["chord"]), float(n["span"]),
                                              float(n.get("taper", 1.0)))
            elif "builtin" in shape:
                b = dict(shape["builtin"])
                meta = self.geometry.add_builtin(str(b.pop("shape")), **{k: float(v) for k, v in b.items()})
            else:
                raise RunRequestError("This lesson step has no built-in shape to import.")
        except (GeometryError, KeyError, TypeError, ValueError) as e:
            raise RunRequestError(f"The lesson's shape could not be made: {e}") from None
        gid = meta["id"]
        _, meta, geom, reference, preset_v = self._prepare(gid, cond.get("preset", "quick"), True)
        airspeed = float(cond.get("airspeed", 20.0))
        try:
            if cond.get("kind") == "sweep":
                case = build_sweep_case(geom, airspeed, cond.get("angles") or [], reference, preset=preset_v,
                                        n_procs=int(pack["params"].get("n_procs") or self.n_procs), runs_root=self.root)
                kind = "sweep"
            else:
                case = build_case(geom, _conditions(airspeed, cond.get("alpha_deg", 0.0)), reference, preset=preset_v,
                                  n_procs=int(pack["params"].get("n_procs") or self.n_procs), runs_root=self.root)
                kind = "single"
        except InputError as e:
            raise RunRequestError(f"This lesson run can't be set up: {e}.") from None
        try:
            lessons.install_pack_files(lesson_id, name, case.path)
        except (lessons.LessonError, OSError, ValueError) as e:
            shutil.rmtree(case.path, ignore_errors=True)
            raise RunRequestError(f"The pre-computed result could not be loaded: {e}") from None
        p = case.params
        ref = meta["reference"]
        params = {
            "kind": kind, "airspeed": p["airspeed"], "alpha_deg": p["alpha_deg"], "angles": p.get("angles"),
            "preset": p["preset"], "reynolds": p["reynolds"], "mach": p["mach"], "reference_area": p["a_ref"],
            "reference_length": p["l_ref"], "reference_how": ref.get("how"), "max_iterations": p["max_iterations"],
            "n_procs": int(pack["params"].get("n_procs") or self.n_procs), "geometry_name": meta.get("name"),
            "geometry_class": meta.get("class"), "source": "lesson pack", "pack": key,
            "pack_computed": pack.get("computed"), "pack_windlab_version": pack.get("windlab_version"),
        }
        now = time.time()
        state = RunState(run_id=case.run_id, case_dir=case.path, geometry_id=gid, params=params, status="done",
                         stage="done", started=now, finished=now)
        state.result = dict(pack["result"])
        state.result["label"] = LABEL
        if kind == "sweep":
            state.points = list(state.result.get("points") or [])
            state.point_index = max(0, len(state.points) - 1)
        state.iteration = int(state.result.get("iterations") or 0)
        state.converged_at = state.iteration if state.result.get("converged") else None
        note = (f"Pre-computed result: the WindLab team ran this case on {pack.get('computed', 'an earlier date')} "
                "(Quick mesh); it was not calculated on this computer.")
        state.warnings = [note, *pack.get("warnings", [])]
        with self._lock:
            self._runs[state.run_id] = state
            self._order.append(state.run_id)
        self._save(state)
        self.geometry.touch(gid)
        self.usage.event("pack")
        return state

    def _find_pack_run(self, key: str) -> RunState | None:
        for summary in self.list_runs():
            if summary.get("status") != "done":
                continue
            try:
                st = self.get(summary["run_id"])
            except (KeyError, RunRequestError):
                continue
            if st.params.get("pack") == key and st.result:
                return st
        return None

    def _prepare(self, geometry_id: str, preset: str, units_confirmed: bool):
        """Checks shared by single runs and sweeps; returns what the case builder needs."""
        try:
            gid = parse_uuid(geometry_id)
            meta = self.geometry.get(gid)
        except (GeometryError, KeyError):
            raise RunRequestError("That shape was not found. Please choose or upload the shape again.") from None
        if not meta.get("usable"):
            raise RunRequestError("This shape can't be simulated until the problems in its check report are fixed.")
        report = meta["report"]
        if report.get("size_suspicious") and not units_confirmed:
            raise RunRequestError(
                f"Your part would be {_fmt_length(report['longest_m'])} long, which looks like a units mistake. "
                "Check the units in Step 1, or tick \"Yes, this size is right\" if it really is that size."
            )
        try:
            preset_v = Preset(preset)
        except ValueError:
            preset_v = None
        if preset_v not in self.available_presets():
            raise RunRequestError("That mesh choice is not available on this computer. Choose Quick or "
                                  "\"More accurate, slower\".")
        free = _free_bytes(self.root)
        if free is not None and free < MIN_FREE_BYTES:
            raise RunRequestError(DISK_LOW.format(mb=free / 2**20))
        ref = meta["reference"]
        try:
            # only an upload gets the thin-feature refinement; built-in wings and bodies keep their meshes
            upload = meta.get("kind") == "upload"
            geom = GeometryInput(
                stl_path=self.geometry.stl_path(gid),
                bounds_min=tuple(report["bounds_min_m"]),
                bounds_max=tuple(report["bounds_max_m"]),
                thickness=report.get("thickness") if upload else None,
                surface_area=report.get("surface_area_m2") if upload else None,
            )
            reference = ReferenceValues(area=ref["area"], length=ref["length"], moment_point=tuple(ref["moment_point"]))
        except KeyError:
            raise RunRequestError("That shape was not found. Please choose or upload the shape again.") from None
        except ValueError as e:
            raise RunRequestError(_plain_validation(e)) from None
        return gid, meta, geom, reference, preset_v

    # ------------------------------------------------------------------ cancel / delete

    def cancel(self, run_id: str) -> RunState:
        """Stop a queued or running run. A running one stops within a few seconds (the
        worker terminates the solver's process group and then marks it cancelled)."""
        st = self.get(run_id)  # KeyError if unknown
        save = False
        with self._lock:
            if st.status == "cancelled":
                return st  # asking twice is fine
            if st.status not in ACTIVE:
                raise RunConflictError("This run has already finished.")
            st.cancel_event.set()
            self.usage.event("cancel")
            if st.status == "queued":  # the worker skips it
                st.status, st.stage, st.finished = "cancelled", "cancelled", time.time()
                save = True
        if save:
            self._save(st)
        return st

    def delete(self, run_id: str) -> None:
        """Remove a finished run and its case folder."""
        st = self.get(run_id)
        if st.status in ACTIVE:
            raise RunConflictError("Stop this run before deleting it.")
        with self._lock:
            self._runs.pop(st.run_id, None)
            if st.run_id in self._order:
                self._order.remove(st.run_id)
        d = st.case_dir
        if d.is_dir() and not d.is_symlink() and d.parent == self.root.resolve():
            shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------ housekeeping

    def active_ids(self) -> set[str]:
        with self._lock:
            return {r.run_id for r in self._runs.values() if r.status in ACTIVE}

    def housekeep(self) -> dict:
        """Prune old runs (count and disk cap) and unused shapes. Never touches active runs."""
        with self._lock:
            active = {r.run_id for r in self._runs.values() if r.status in ACTIVE}
            active_geo = {r.geometry_id for r in self._runs.values() if r.status in ACTIVE}
        runs = housekeeping.prune_runs(self.root, self.data_root, active, keep=self.keep_runs,
                                       cap_bytes=self.disk_cap_bytes)
        with self._lock:
            for rid in runs:
                self._runs.pop(rid, None)
                if rid in self._order:
                    self._order.remove(rid)
        shapes = housekeeping.prune_geometry(self.geometry.root, self.root, active_geo,
                                             max_age_days=self.geometry_max_age_days)
        if self.incoming is not None:
            housekeeping.prune_incoming(self.incoming)
        meshes = self.cache.housekeep() if self.cache is not None else []
        return {"runs_removed": runs, "shapes_removed": shapes, "meshes_removed": meshes}

    def _housekeep_quietly(self) -> None:
        try:
            self.housekeep()
        except Exception:  # housekeeping must never break a run
            log.exception("housekeeping failed")

    # ------------------------------------------------------------------ presets and time estimates

    def available_presets(self) -> list[Preset]:
        """Quick and Standard always; Detailed only with >= 24 GB for Linux and a measured
        cold Quick run under 90 s (windlab.speed)."""
        out = [p for p in speed.available_presets(self.data_root, machine.meminfo()["total"])
               if p in EXPOSED_PRESETS or p == Preset.DETAILED]
        return out

    def preset_choices(self) -> list[dict]:
        return [{"id": p.value, "label": PRESET_LABELS[p][0], "description": PRESET_LABELS[p][1],
                 "cells": PRESETS[p].target_cells} for p in self.available_presets()]

    def thin_features_for(self, meta: dict) -> dict:
        """Per offered preset: what is thin on this uploaded shape at that mesh's surface cell, with the
        plain-language notes the page shows before a run (windlab.geometry.thin); {} when nothing is thin
        or the shape is built in."""
        if meta.get("kind") != "upload" or not meta.get("usable"):
            return {}
        report = meta.get("report") or {}
        length = (meta.get("reference") or {}).get("length")
        if not length or not report.get("thickness"):
            return {}
        out = {}
        for p in self.available_presets():
            cell = PRESETS[p].bg_cell_over_l * float(length) / 2 ** SURFACE_LEVELS[1]
            found = thin.thin_features(report.get("thickness"), cell, report.get("surface_area_m2"), n_layers=N_LAYERS)
            if found:
                out[p.value] = {**found, "notes": thin.notes(found, PRESET_LABELS[p][0])}
        return out

    def quick_cells_for(self, geometry_id: str | None) -> int | None:
        """Cells of this shape's latest finished Quick run (the time estimate scales from it)."""
        if not geometry_id:
            return None
        try:
            gid = parse_uuid(geometry_id)
        except GeometryError:
            return None
        for summary in self.list_runs():
            if summary.get("status") != "done" or summary.get("source"):
                continue
            try:
                st = self.get(summary["run_id"])
            except (KeyError, RunRequestError, OSError, ValueError):
                continue
            if st.geometry_id == gid and st.params.get("preset") == "quick" and (st.result or {}).get("cells"):
                return int(st.result["cells"])
        return None

    def estimates(self, geometry_id: str | None = None, n_angles: int = 1) -> dict:
        """Expected run time per available preset, from this computer's measured speed."""
        cells = self.quick_cells_for(geometry_id)
        n = max(1, min(int(n_angles), MAX_SWEEP_ANGLES))
        out = {p.value: speed.estimate(self.data_root, p, self.n_procs, cells, n) for p in self.available_presets()}
        sp = speed.machine_speed(self.data_root)
        return {"presets": out, "basis": sp["basis"], "n_angles": n, "n_procs": self.n_procs,
                "quick_cells": cells, "runs_measured": sp["runs"]}

    def _record_speed(self, state: RunState, rep: dict) -> None:
        """Keep this run's solver and meshing speed for the time estimates (never fails a run)."""
        try:
            with self._lock:
                stages = dict(state.stage_times)  # the app's own wall clock per stage
                if state.stage in STAGES and state.stage_started is not None:  # the stage still "running"
                    stages[state.stage] = stages.get(state.stage, 0.0) + time.time() - state.stage_started
            mesh_s = sum(v for k, v in stages.items() if k in eta.MESH_STAGES)
            p = state.params
            n_angles = len(p.get("angles") or [0])
            speed.record_run(self.data_root, preset=p.get("preset", "quick"), n_procs=int(p.get("n_procs") or 1),
                             cells=rep.get("mesh", {}).get("cells"), iterations=rep.get("iterations"),
                             solver_seconds=stages.get("simpleFoam"), mesh_seconds=mesh_s or None,
                             total_seconds=rep.get("total_seconds"), kind=p.get("kind", "single"),
                             n_angles=n_angles, warm=bool(p.get("warm_start")), mesh_reused=bool(p.get("mesh_reused")))
        except Exception:  # noqa: BLE001
            log.exception("could not record the speed of run %s", state.run_id)

    # ------------------------------------------------------------------ worker

    def _work(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            with self._lock:
                state = self._runs.get(run_id)
                if state is None or state.status != "queued":  # cancelled or deleted while waiting
                    continue
                state.status, state.started = "running", time.time()
            try:
                self._execute(state)
            except Exception as e:  # never let one run kill the worker
                log.exception("run %s crashed", run_id)
                msg = DISK_FULL_RUN if _disk_full(e, state.case_dir) else INTERNAL_ERROR
                try:
                    self._fail(state, msg)
                except Exception:  # e.g. the disk is full and the status can't be saved
                    log.exception("could not record the failure of run %s", run_id)
                    with self._lock:
                        state.status, state.stage, state.error, state.finished = "failed", "failed", msg, time.time()
            self._housekeep_quietly()

    def _set_stage(self, state: RunState, stage: str) -> None:
        now = time.time()
        with self._lock:
            if state.stage_started is not None and state.stage in STAGES:
                state.stage_times[state.stage] = state.stage_times.get(state.stage, 0.0) + now - state.stage_started
            state.stage, state.stage_started = stage, now

    def _execute(self, state: RunState) -> None:
        sweep = state.params.get("kind") == "sweep"

        def progress(kind: str, data: dict) -> None:
            if kind == "stage":
                self._set_stage(state, data["stage"])
            elif kind == "iteration":
                state.iteration = int(data["iteration"])
            elif kind == "converged":
                state.converged_at = int(data["iteration"])
            elif kind == "point":
                with self._lock:
                    state.point_index, state.converged_at, state.iteration = int(data["index"]), None, 0
                    if state.point_index < len(state.points):
                        state.points[state.point_index]["status"] = "running"
                # the next angle starts now: never leave "surface" (of the previous angle) as the
                # stage, or the time-left estimate would think the last angle is already solved
                self._set_stage(state, "potentialFoam")
            elif kind == "mesh_reused":
                with self._lock:
                    state.params["mesh_reused"] = True
            elif kind == "warm_start":
                with self._lock:
                    state.params["warm_start"] = {k: data.get(k) for k in ("alpha_deg", "airspeed")}

        def on_point(i: int, res: dict) -> None:
            self._set_stage(state, "surface")
            self._point_done(state, i, res)
            self._save(state)

        extra: dict = {"on_point": on_point} if sweep else {}
        if self.cache is not None and self._runner_takes_cache:
            extra["cache"] = self.cache
        try:
            report = self.runner(state.case_dir, progress=progress, cancel=state.cancel_event, **extra)
        except RunCancelled:
            for p in state.points:
                if p.get("status") in ("waiting", "running"):
                    p["status"] = "cancelled"
            self._end(state, "cancelled")
            return
        except RunError as e:
            self._fail(state, _plain_run_error(e, state.case_dir))
            return
        rep = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        self._record_speed(state, rep)
        report_file = state.case_dir / "windlab_report.json"
        if not report_file.exists():  # the real runner writes it; keep it for the report export whatever ran
            try:
                report_file.write_text(json.dumps(rep, indent=2, default=str))
            except (OSError, TypeError, ValueError) as e:
                log.warning("could not write the report of %s: %s", state.run_id, e)
        if sweep:
            self._finish_sweep(state, rep)
            return
        self._set_stage(state, "surface")
        warnings = run_warnings(rep)
        warnings += symmetry_notes(state.params, rep.get("coefficients", {}), state.params.get("alpha_deg"))
        warnings += thin_notes(state.params)
        try:
            blob = surface.pressure_surface(state.case_dir, state.params["airspeed"])
            (state.case_dir / "windlab_surface.bin").write_bytes(blob)
        except (surface.SurfaceError, OSError, ValueError) as e:
            log.warning("surface for %s: %s", state.run_id, e)
            warnings.append("The 3D pressure picture could not be made for this run.")
        if not self._make_slice(state, None):
            warnings.append("The flow slice picture could not be made for this run.")
        c = rep.get("coefficients", {})
        state.result = {
            "cl": c.get("cl"),
            "cd": c.get("cd"),
            "cm": c.get("cm"),
            "cl_pm": c.get("cl_pm"),
            "cd_pm": c.get("cd_pm"),
            "l_over_d": c.get("l_over_d"),
            "iterations": rep.get("iterations"),
            "converged": rep.get("converged"),
            "cells": rep.get("mesh", {}).get("cells"),
            "mesh_reused": bool(rep.get("mesh", {}).get("reused")),
            "warm_start": rep.get("warm_start"),
            "solver_seconds": rep.get("total_seconds"),
            "peak_memory_mb": rep.get("peak_rss_mb"),
            "label": LABEL,
        }
        state.warnings = warnings
        self._end(state, "done")

    def _point_done(self, state: RunState, i: int, res: dict) -> None:
        """One sweep angle finished: its numbers, warnings and pressure picture."""
        point = {"alpha_deg": res.get("alpha_deg"), "status": res.get("status", "failed"),
                 "seconds": res.get("seconds")}
        if point["status"] == "done":
            c = res.get("coefficients", {})
            point.update({
                "cl": c.get("cl"), "cd": c.get("cd"), "cm": c.get("cm"), "l_over_d": c.get("l_over_d"),
                "cl_pm": c.get("cl_pm"), "cd_pm": c.get("cd_pm"), "iterations": res.get("iterations"),
                "converged": res.get("converged"), "warm_start": res.get("warm_start"),
                "warnings": run_warnings({"converged": res.get("converged"), "coefficients": c})
                + symmetry_notes(state.params, c, res.get("alpha_deg")) + thin_notes(state.params),
            })
            try:
                blob = surface.pressure_surface(state.case_dir, state.params["airspeed"])
                (state.case_dir / f"windlab_surface_{int(i)}.bin").write_bytes(blob)
                point["surface"] = True
            except (surface.SurfaceError, OSError, ValueError) as e:
                log.warning("surface for %s point %d: %s", state.run_id, i, e)
                point["surface"] = False
            point["slice"] = self._make_slice(state, i)
        else:
            point["error"] = ("The airflow calculation did not finish at this angle: it became unstable. "
                              "This often happens past stall; try smaller angles or a lower airspeed.")
        with self._lock:
            if 0 <= i < len(state.points):
                state.points[i] = point

    def _make_slice(self, state: RunState, point: int | None) -> bool:
        """Mid-span flow slice from the solver's midPlane output (still in postProcessing)."""
        airspeed = state.params["airspeed"]
        try:
            data = flow_slice.read_slice(state.case_dir)
            blob = flow_slice.flow_slice(state.case_dir, airspeed, data=data)
        except (surface.SurfaceError, OSError, ValueError, KeyError) as e:
            log.warning("slice for %s (point %s): %s", state.run_id, point, e)
            return False
        suffix = "" if point is None else f"_{int(point)}"
        (state.case_dir / f"windlab_slice{suffix}.bin").write_bytes(blob)
        try:  # windows and streamlines: optional extras for the picture
            d = flow_slice.decode(blob)
            meta = json.loads((state.case_dir / "windlab_case.json").read_text())
            info = flow_slice.slice_info(meta, [d["x_min"], d["x_max"], d["z_min"], d["z_max"]], d["y"],
                                         data=data, airspeed=airspeed)
            (state.case_dir / f"windlab_sliceinfo{suffix}.json").write_text(json.dumps(info))
        except (surface.SurfaceError, OSError, ValueError, KeyError, TypeError) as e:
            log.warning("streamlines for %s (point %s): %s", state.run_id, point, e)
        return True

    def slice_info(self, run_id: str, point: int | None = None) -> dict:
        """Windows, body box and streamlines for the flow slice (computed at the end of the
        run; for older runs without them: windows and body box only)."""
        st = self.get(run_id)
        point = self._point(st, point)
        f = self._picture_file(st, point, "slice")
        if st.status not in ("done", "running") or not f.is_file() or f.is_symlink():
            raise KeyError(run_id)
        info_f = f.with_name(f.name.replace("windlab_slice", "windlab_sliceinfo").replace(".bin", ".json"))
        if info_f.is_file() and not info_f.is_symlink():
            return json.loads(info_f.read_text())
        d = flow_slice.decode(f.read_bytes())
        meta = json.loads((st.case_dir / "windlab_case.json").read_text())
        return flow_slice.slice_info(meta, [d["x_min"], d["x_max"], d["z_min"], d["z_max"]], d["y"])

    def _finish_sweep(self, state: RunState, rep: dict) -> None:
        done = [p for p in state.points if p.get("status") == "done"]
        summary = sweep_summary(state.points)
        state.result = {
            "points": state.points,
            **summary,
            "iterations": rep.get("iterations"),
            "converged": rep.get("converged"),
            "cells": rep.get("mesh", {}).get("cells"),
            "mesh_reused": bool(rep.get("mesh", {}).get("reused")),
            "solver_seconds": rep.get("total_seconds"),
            "peak_memory_mb": rep.get("peak_rss_mb"),
            "label": LABEL,
        }
        warnings = run_warnings({"converged": True, "mesh": rep.get("mesh", {})})
        failed = [p["alpha_deg"] for p in state.points if p.get("status") == "failed"]
        if failed:
            warnings.append("No result at " + ", ".join(f"{a:g}°" for a in failed) +
                            ": the calculation did not finish there (often a sign of stalled, unsteady flow). "
                            "Try smaller angles or a lower airspeed for those points.")
        unsettled = [p["alpha_deg"] for p in done if not p.get("converged")]
        if unsettled:
            warnings.append("The numbers did not fully settle at " + ", ".join(f"{a:g}°" for a in unsettled) +
                            ". Treat those points as a rough guide.")
        state.warnings = warnings
        if not done:
            self._fail(state, "The airflow calculation did not finish at any of the angles.")
            return
        self._end(state, "done")

    def _fail(self, state: RunState, message: str) -> None:
        self._end(state, "failed", message)

    def _end(self, state: RunState, status: str, error: str | None = None) -> None:
        """Slim the case folder (results are extracted by now), then record the final status."""
        if status == "done":
            secs = (state.result or {}).get("solver_seconds") or (time.time() - (state.started or time.time()))
            self.usage.event("done", seconds=secs)
        elif status == "failed":
            self.usage.event("error", message=error, stage=state.stage)
        try:
            housekeeping.slim_case(state.case_dir)
        except OSError as e:
            log.warning("could not slim %s: %s", state.run_id, e)
        self._set_stage(state, status)
        with self._lock:
            state.status, state.finished = status, time.time()
            if error:
                state.error = error
        self._save(state)

    def _save(self, state: RunState) -> None:
        f = state.case_dir / "windlab_status.json"
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(state.to_json(), indent=2))
        tmp.replace(f)

    # ------------------------------------------------------------------ read

    def case_dir(self, run_id: str) -> Path:
        try:
            rid = parse_uuid(run_id)
        except GeometryError:
            raise RunRequestError("That run id is not valid.") from None
        return safe_join(self.root, rid)

    def get(self, run_id: str) -> RunState:
        d = self.case_dir(run_id)
        rid = d.name
        with self._lock:
            st = self._runs.get(rid)
        if st is not None:
            return st
        f = d / "windlab_status.json"
        if not f.is_file() or f.is_symlink():
            raise KeyError(run_id)
        data = json.loads(f.read_text())
        st = RunState(run_id=rid, case_dir=d, geometry_id=data["geometry_id"], params=data["params"])
        for k in ("status", "stage", "created", "started", "finished", "stage_times", "iteration", "converged_at",
                  "result", "warnings", "error"):
            setattr(st, k, data.get(k))
        st.points, st.point_index = data.get("points") or [], data.get("point_index") or 0
        if st.status in ("queued", "running"):  # server stopped mid-run
            st.status, st.stage = "failed", "failed"
            st.error = "This run was interrupted (WindLab was closed while it was running). Please run it again."
        return st

    def list_runs(self, limit: int = MAX_LISTED_RUNS) -> list[dict]:
        """Short summaries of every run on disk (and in memory), newest first."""
        ids = set()
        for d in self.root.iterdir():
            try:
                ids.add(parse_uuid(d.name))
            except GeometryError:
                continue
        with self._lock:
            ids |= set(self._runs)
        out = []
        for rid in ids:
            try:
                st = self.get(rid)
            except (KeyError, RunRequestError, OSError, ValueError):
                continue
            out.append(run_summary(st))
        out.sort(key=lambda s: s["created"] or 0, reverse=True)
        return out[:limit]

    def queue_position(self, run_id: str) -> int:
        with self._lock:
            waiting = [r for r in self._order if self._runs[r].status == "queued"]
            running = any(self._runs[r].status == "running" for r in self._order)
        if run_id not in waiting:
            return 0
        return waiting.index(run_id) + (1 if running else 0)

    def _point(self, st: RunState, point: int | None) -> int | None:
        """Validated sweep point index (None for single runs / not given)."""
        if point is None:
            return None
        if st.params.get("kind") != "sweep" or not 0 <= int(point) < len(st.points):
            raise RunRequestError("That angle is not part of this run.")
        return int(point)

    def history(self, st: RunState, point: int | None = None) -> list[list[float]]:
        if st.status == "queued":
            return []
        if st.params.get("kind") != "sweep":
            return read_history(st.case_dir)
        if point is None:  # live: the angle being solved; afterwards: the last one
            if st.status == "running":
                return read_history(st.case_dir)
            point = st.point_index
        return read_history(point_dir(st.case_dir, point))

    def status(self, run_id: str, point: int | None = None) -> dict:
        st = self.get(run_id)
        point = self._point(st, point)
        history = self.history(st, point)
        live = point is None or (st.status == "running" and point == st.point_index)
        it = history[-1][0] if history and live else st.iteration
        out = st.to_json()
        out.pop("params", None)
        stage_label = STAGES.get(st.stage, (st.stage, 0))[0]
        if st.params.get("kind") == "sweep" and st.status == "running" and st.stage in (
                "potentialFoam", "simpleFoam", "surface"):
            a = st.points[st.point_index]["alpha_deg"] if st.point_index < len(st.points) else None
            stage_label += f" (angle {st.point_index + 1} of {len(st.points)}: {a:g}°)"
        out.update({
            "params": st.params,
            "stage_label": stage_label,
            "iteration": it,
            "history": history,
            "history_point": point,
            "queue_position": self.queue_position(st.run_id),
            "cancel_requested": st.cancel_event.is_set(),
            "label": LABEL,
        })
        out.update(progress_estimate(st, it))
        if st.status == "done" and st.result:
            out["result"] = with_expected_ranges(st)
            out["points"] = out["result"].get("points", out["points"])
        out["surface_available"] = self._picture_file(st, None).is_file()
        out["slice_available"] = self._picture_file(st, None, "slice").is_file()
        if st.params.get("kind") == "sweep":
            out["surface_points"] = [i for i in range(len(st.points)) if self._picture_file(st, i).is_file()]
            out["slice_points"] = [i for i in range(len(st.points)) if self._picture_file(st, i, "slice").is_file()]
        return out

    def _picture_file(self, st: RunState, point: int | None, what: str = "surface") -> Path:
        if st.params.get("kind") == "sweep":
            if point is None:  # default picture: the best-L/D angle, else the first one
                best = (st.result or {}).get("max_l_over_d_alpha")
                idx = [i for i, p in enumerate(st.points) if p.get("alpha_deg") == best] or [0]
                point = idx[0]
            return st.case_dir / f"windlab_{what}_{int(point)}.bin"
        return st.case_dir / f"windlab_{what}.bin"

    def surface_blob(self, run_id: str, point: int | None = None, what: str = "surface") -> bytes:
        st = self.get(run_id)
        point = self._point(st, point)
        f = self._picture_file(st, point, what)
        if st.status not in ("done", "running") or not f.is_file() or f.is_symlink():
            raise KeyError(run_id)
        return f.read_bytes()

    def results_csv(self, run_id: str) -> tuple[str, str]:
        """(file name, CSV text) of a finished run's coefficients, units in the header."""
        st = self.get(run_id)
        if st.status != "done" or not st.result:
            raise RunConflictError("Results can be downloaded once the run has finished.")
        return csv_name(st, "results"), results_csv(st)

    # ------------------------------------------------------------------ compare

    def compare(self, run_ids) -> dict:
        """Side-by-side numbers of 2 to 5 finished runs (single runs or sweeps), with plain
        warnings when they are not directly comparable."""
        if isinstance(run_ids, str):
            run_ids = [r for r in run_ids.split(",") if r.strip()]
        ids = []
        for raw in run_ids:
            try:
                rid = parse_uuid(str(raw).strip())
            except GeometryError:
                raise RunRequestError("One of the runs to compare is not a valid run id.") from None
            if rid not in ids:
                ids.append(rid)
        if not MIN_COMPARE <= len(ids) <= MAX_COMPARE:
            raise RunRequestError(f"Choose {MIN_COMPARE} to {MAX_COMPARE} different finished runs to compare.")
        states = []
        for rid in ids:
            try:
                st = self.get(rid)
            except KeyError:
                raise KeyError(rid) from None
            if st.status != "done" or not st.result:
                raise RunConflictError("Only finished runs can be compared. One of the chosen runs has not "
                                       "finished or stopped with a problem.")
            states.append(st)
        runs = [compare_entry(st, i) for i, st in enumerate(states)]
        return {"runs": runs, "warnings": compare_warnings(runs), "label": LABEL}

    def compare_csv(self, run_ids) -> tuple[str, str]:
        data = self.compare(run_ids)
        ids = "-".join(r["run_id"][:4] for r in data["runs"])
        name = f"windlab-compare-{time.strftime('%Y-%m-%d')}-{ids}.csv"
        return name, compare_csv(data)

    def history_csv(self, run_id: str, point: int | None = None) -> tuple[str, str]:
        st = self.get(run_id)
        point = self._point(st, point)
        hist = self.history(st, point)
        if not hist:
            raise RunConflictError("There is no solver history for this run yet.")
        return csv_name(st, "history" if point is None else f"history-{point}"), history_csv(st, hist, point)


# --------------------------------------------------------------------------- helpers


STALL_NOTE = ("Lift stops rising above about {a:g}° (highest C_L {cl:.2f}). This may be the start of stall, "
              "where the flow separates from the upper surface. RANS stall predictions are uncertain, so treat "
              "the numbers from this angle on with care.")


def sweep_summary(points: list[dict]) -> dict:
    """Best L/D point and a plain-language note if C_L stops rising with the angle."""
    done = sorted((p for p in points if p.get("status") == "done" and p.get("cl") is not None),
                  key=lambda p: p["alpha_deg"])
    out: dict = {"max_l_over_d": None, "max_l_over_d_alpha": None, "cl_max": None, "cl_max_alpha": None,
                 "stall_alpha": None, "stall_note": None}
    with_ld = [p for p in done if p.get("cd") and p.get("l_over_d") is not None]
    if with_ld:
        best = max(with_ld, key=lambda p: p["l_over_d"])
        out["max_l_over_d"], out["max_l_over_d_alpha"] = best["l_over_d"], best["alpha_deg"]
    if done:
        top = max(done, key=lambda p: p["cl"])
        out["cl_max"], out["cl_max_alpha"] = top["cl"], top["alpha_deg"]
    for a, b in zip(done, done[1:], strict=False):
        if b["cl"] <= a["cl"]:
            out["stall_alpha"] = a["alpha_deg"]
            out["stall_note"] = STALL_NOTE.format(a=a["alpha_deg"], cl=a["cl"])
            break
    return out


RESULT_COLUMNS = ["angle of attack [deg]", "airspeed [m/s]", "Reynolds number [-]", "CL [-]", "CD [-]", "CM [-]",
                  "L/D [-]", "lift [N]", "drag [N]", "iterations [-]", "settled [yes/no]", "status",
                  "CL likely low [-]", "CL likely high [-]", "CD likely low [-]", "CD likely high [-]",
                  "expected error [validated/unvalidated]"]


def with_expected_ranges(st: RunState) -> dict:
    """A copy of the run's result with the expected-error ranges (PRD RES-2) attached: under
    ``expected`` for a single run, under each point's ``expected`` for a sweep."""
    r = dict(st.result or {})
    cls = expected_error.geometry_class_of(st.params)
    if st.params.get("kind") == "sweep":
        pts = []
        for pt in r.get("points") or st.points:
            pt = dict(pt)
            if pt.get("status") == "done":
                pt["expected"] = expected_error.expected_for_point(_with_oscillation(pt), st.params, cls)
            pts.append(pt)
        r["points"] = pts
    else:
        r["expected"] = expected_error.expected_for_point(_with_oscillation(r), st.params, cls)
    r["validation_table"] = expected_error.table_version()
    return r


def _with_oscillation(pt: dict) -> dict:
    if "cl_oscillation" not in pt and pt.get("cl_pm") is not None and pt.get("cl") is not None:
        return {**pt, "cl_oscillation": 2 * pt["cl_pm"] / max(abs(pt["cl"]), 0.05)}
    return pt


def _range_cells(exp: dict | None) -> list[str]:
    """The five expected-error CSV cells of one result."""
    if not exp:
        return ["", "", "", "", ""]
    cl, cd = exp.get("cl") or {}, exp.get("cd") or {}
    status = "validated" if cl.get("status") == "validated" and cd.get("status") == "validated" else "unvalidated"
    return [_fmt(cl.get("low")), _fmt(cl.get("high")), _fmt(cd.get("low")), _fmt(cd.get("high")), status]


def csv_name(st: RunState, what: str) -> str:
    day = time.strftime("%Y-%m-%d", time.localtime(st.created or time.time()))
    return f"windlab-{st.params.get('kind', 'single')}-{day}-{st.run_id[:8]}-{what}.csv"


def csv_comment(text: str) -> str:
    """One CSV comment line: '# ' + text with no commas, quotes, '#' or line breaks, so
    Excel shows it in one cell and pandas.read_csv(comment='#') skips it."""
    clean = " ".join(str(text).split())
    for a, b in ((",", ";"), ('"', "'"), ("#", "no. ")):
        clean = clean.replace(a, b)
    return "# " + clean + "\n"


def _csv_head(st: RunState) -> str:
    p, r = st.params, st.result or {}
    lines = [
        "WindLab results - uncalibrated prototype - not yet validated",
        f"shape: {p.get('geometry_name') or '-'}",
        f"reference area [m^2]: {p.get('reference_area'):.6g}; reference length [m]: "
        f"{p.get('reference_length'):.6g} ({p.get('reference_how') or '-'})",
        f"air: density [kg/m^3] {RHO_SEA_LEVEL:g}; kinematic viscosity [m^2/s] {NU_SEA_LEVEL:g}",
        f"mesh: {p.get('preset', 'quick')}; {r.get('cells') or '-'} cells; run {st.run_id}; "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(st.created or 0))}",
        "CL/CD/CM: mean over the last 100 solver iterations of a settled run (200 if it hit the iteration limit); "
        "lift and drag from CL and CD at this airspeed",
        f"expected error: 'likely low/high' is where the true value most likely lies (validation table "
        f"{expected_error.table_version()}); 'unvalidated' = outside the checked shapes, Reynolds numbers or angles - "
        "treat as a trend only",
        disclaimer.FOOTER_ASCII,
    ]
    return "".join(csv_comment(t) for t in lines)


def _fmt(x, digits: int = 6) -> str:
    return "" if x is None else f"{x:.{digits}g}"


def results_csv(st: RunState) -> str:
    p = st.params
    r = with_expected_ranges(st) if st.result else {}
    q_a = 0.5 * RHO_SEA_LEVEL * p["airspeed"] ** 2 * p["reference_area"]
    re_ = p.get("reynolds")
    if p.get("kind") == "sweep":
        rows = [(pt.get("alpha_deg"), pt) for pt in r.get("points") or st.points]
    else:
        rows = [(p.get("alpha_deg"), {**r, "status": st.status})]
    buf = io.StringIO()
    buf.write(_csv_head(st))
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(RESULT_COLUMNS)
    for alpha, pt in rows:
        done = pt.get("status") == "done"
        cl, cd = (pt.get("cl"), pt.get("cd")) if done else (None, None)
        w.writerow([_fmt(alpha), _fmt(p["airspeed"]), _fmt(re_), _fmt(cl), _fmt(cd),
                    _fmt(pt.get("cm") if done else None), _fmt(pt.get("l_over_d") if done else None, 4),
                    _fmt(None if cl is None else q_a * cl, 4), _fmt(None if cd is None else q_a * cd, 4),
                    pt.get("iterations") or "", ("yes" if pt.get("converged") else "no") if done else "",
                    pt.get("status", ""), *_range_cells(pt.get("expected") if done else None)])
    return buf.getvalue()


def history_csv(st: RunState, hist: list, point: int | None) -> str:
    buf = io.StringIO()
    buf.write(_csv_head(st))
    if point is not None:
        buf.write(csv_comment(f"angle of attack [deg]: {st.points[point]['alpha_deg']:g}"))
    elif st.params.get("alpha_deg") is not None:
        buf.write(csv_comment(f"angle of attack [deg]: {st.params['alpha_deg']:g}"))
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["iteration [-]", "CL [-]", "CD [-]"])
    w.writerows([[int(i), f"{cl:.6g}", f"{cd:.6g}"] for i, cl, cd in hist])
    return buf.getvalue()


MIN_COMPARE, MAX_COMPARE = 2, 5  # 5: the Streamlining lesson compares its five bodies at once
COMPARE_COLUMNS = ["run", "shape", "kind", "airspeed [m/s]", "Reynolds number [-]", "reference area [m^2]",
                   "reference length [m]", "angle of attack [deg]", "CL [-]", "CD [-]", "CM [-]", "L/D [-]",
                   "lift [N]", "drag [N]", "settled [yes/no]", "CL likely low [-]", "CL likely high [-]",
                   "CD likely low [-]", "CD likely high [-]", "expected error [validated/unvalidated]"]


def compare_entry(st: RunState, index: int) -> dict:
    """One run in a comparison: its conditions and one point per finished angle."""
    p, r = st.params, with_expected_ranges(st) if st.result else {}
    if p.get("kind") == "sweep":
        pts = [pt for pt in (r.get("points") or st.points) if pt.get("status") == "done"]
    else:
        pts = [{**r, "alpha_deg": p.get("alpha_deg")}]
    points = [{"alpha_deg": pt.get("alpha_deg"), "cl": pt.get("cl"), "cd": pt.get("cd"), "cm": pt.get("cm"),
               "l_over_d": pt.get("l_over_d"), "converged": pt.get("converged"), "expected": pt.get("expected")}
              for pt in pts]
    points.sort(key=lambda q: q["alpha_deg"] if q["alpha_deg"] is not None else 0)
    letter = "ABCDE"[index] if index < 5 else str(index + 1)
    return {
        "run_id": st.run_id,
        "letter": letter,
        "name": p.get("geometry_name") or "run",
        "kind": p.get("kind", "single"),
        "airspeed": p.get("airspeed"),
        "reynolds": p.get("reynolds"),
        "reference_area": p.get("reference_area"),
        "reference_length": p.get("reference_length"),
        "reference_how": p.get("reference_how"),
        "preset": p.get("preset"),
        "geometry_class": expected_error.geometry_class_of(p),
        "created": st.created,
        "points": points,
    }


def reference_convention(how: str | None) -> str:
    """Which convention a run's reference area follows, from the plain-language ``how`` text
    the geometry store writes: 'frontal area' (bodies), 'planform area' (wings), 'plan-view
    box area' (uploads), 'set by you' (typed in) or 'unknown'."""
    h = (how or "").lower()
    if "set by you" in h:
        return "set by you"
    for key in ("frontal area", "planform area", "plan-view box"):
        if key in h:
            return key if key != "plan-view box" else "plan-view box area"
    return "unknown"


def _differ(values, rel: float = 0.01) -> bool:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return False
    lo, hi = min(vals), max(vals)
    return hi - lo > rel * max(abs(hi), abs(lo), 1e-12)


def _fmt_reynolds(re_: float) -> str:
    return f"{re_ / 1e6:.2f} million" if re_ >= 1e6 else f"{round(re_):,}"


def compare_warnings(runs: list[dict]) -> list[str]:
    """Plain-language reasons why the coefficients of these runs may not be directly comparable."""
    out = []

    def by(key: str) -> list:
        return [r[key] for r in runs]

    letters = [r["letter"] for r in runs]
    names = " and ".join(letters) if len(letters) < 3 else ", ".join(letters[:-1]) + " and " + letters[-1]
    if _differ(by("reference_area")):
        areas = " / ".join(f"{r['letter']} {r['reference_area']:.3g} m²" for r in runs)
        conventions = {reference_convention(r.get("reference_how")) for r in runs}
        if conventions == {"frontal area"}:
            # the Streamlining lesson: every body on its own frontal area is the textbook comparison
            out.append(f"Each run's coefficients use its own frontal area ({areas}), the standard reference for "
                       "bodies: that is exactly how handbook drag coefficients are defined, so the CD values can be "
                       "compared directly. The forces in newtons differ with the area, of course.")
        elif conventions == {"planform area"}:
            out.append(f"Each wing's coefficients use its own planform area ({areas}), the standard reference for "
                       "wings, so CL and CD can be compared directly as shape-to-shape numbers. The lift and drag "
                       "forces differ with the area: a bigger wing makes more lift at the same CL.")
        else:
            how = " / ".join(f"{r['letter']} {reference_convention(r.get('reference_how'))}" for r in runs)
            out.append(f"These runs used different reference areas ({areas}) following different conventions "
                       f"({how}), so their coefficients are not directly comparable. Coefficients are forces "
                       "divided by the reference area: a bigger area gives smaller CL and CD for the same force. "
                       "Compare the forces instead, or set the same kind of reference area for every run in Step 1.")
    if _differ(by("airspeed")):
        speeds = " / ".join(f"{r['letter']} {r['airspeed']:g} m/s" for r in runs)
        out.append(f"These runs used different airspeeds ({speeds}), so their Reynolds numbers differ. Part of the "
                   "difference in CL and CD comes from the Reynolds number, not only from the shape.")
    elif _differ(by("reynolds")):
        res = " / ".join(f"{r['letter']} {_fmt_reynolds(r['reynolds'])}" for r in runs)
        out.append(f"These runs have different Reynolds numbers ({res}) because their reference lengths differ. "
                   "Part of the difference in CL and CD comes from the Reynolds number, not only from the shape.")
    if len({r["preset"] for r in runs}) > 1:
        out.append("These runs used different mesh sizes; small differences may come from the mesh.")
    if not out:
        out.append(f"Runs {names} used the same airspeed and reference area and have the same Reynolds number: "
                   "their coefficients can be compared directly.")
    return out


def compare_csv(data: dict) -> str:
    buf = io.StringIO()
    buf.write(csv_comment("WindLab run comparison - uncalibrated prototype - not yet validated"))
    buf.write(csv_comment(f"expected error: 'likely low/high' is where the true value most likely lies (validation "
                          f"table {expected_error.table_version()}); 'unvalidated' = treat as a trend only"))
    buf.write(csv_comment(disclaimer.FOOTER_ASCII))
    for r in data["runs"]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created"] or 0))
        buf.write(csv_comment(f"run {r['letter']}: {r['name']}; {r['kind']}; run id {r['run_id']}; {when}"))
    for w in data["warnings"]:
        buf.write(csv_comment("note: " + w))
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COMPARE_COLUMNS)
    for r in data["runs"]:
        q_a = 0.5 * RHO_SEA_LEVEL * r["airspeed"] ** 2 * r["reference_area"]
        for pt in r["points"]:
            cl, cd = pt["cl"], pt["cd"]
            w.writerow([r["letter"], _csv_cell(r["name"]), r["kind"], _fmt(r["airspeed"]), _fmt(r["reynolds"]),
                        _fmt(r["reference_area"]), _fmt(r["reference_length"]), _fmt(pt["alpha_deg"]), _fmt(cl),
                        _fmt(cd), _fmt(pt["cm"]), _fmt(pt["l_over_d"], 4),
                        _fmt(None if cl is None else q_a * cl, 4), _fmt(None if cd is None else q_a * cd, 4),
                        "yes" if pt.get("converged") else "no", *_range_cells(pt.get("expected"))])
    return buf.getvalue()


def _csv_cell(text: str) -> str:
    """Free text in a data cell: no '#' (pandas would cut the line there) or line breaks."""
    return " ".join(str(text).split()).replace("#", "no. ")


def run_summary(st: RunState) -> dict:
    p, r = st.params or {}, st.result or {}
    return {
        "run_id": st.run_id,
        "kind": p.get("kind", "single"),
        "geometry_name": p.get("geometry_name"),
        "airspeed": p.get("airspeed"),
        "alpha_deg": p.get("alpha_deg"),
        "angles": p.get("angles"),
        "cl": r.get("cl"),
        "cd": r.get("cd"),
        "status": st.status,
        "created": st.created,
        "finished": st.finished,
        "source": p.get("source"),
        "pack": p.get("pack"),
    }


def _conditions(airspeed, alpha_deg) -> FlowConditions:
    lo_v, hi_v = 1.0, 100.0
    lo_a, hi_a = -20.0, 20.0
    try:
        v, a = float(airspeed), float(alpha_deg)
    except (TypeError, ValueError):
        raise RunRequestError("Airspeed and angle of attack must be numbers.") from None
    if not (lo_v <= v <= hi_v):  # also rejects NaN
        raise RunRequestError(f"Airspeed must be between {lo_v:g} and {hi_v:g} m/s (WindLab models low-speed air).")
    if not (lo_a <= a <= hi_a):
        raise RunRequestError(f"Angle of attack must be between {lo_a:g} and {hi_a:g} degrees.")
    return FlowConditions(airspeed=v, alpha_deg=a)


def _plain_validation(e: ValueError) -> str:
    text = str(e)
    if "units" in text:
        return "This part's size is outside what WindLab can simulate (1 mm to 100 m). Check the units."
    return "This shape can't be used for a run. Please check it again."


MIN_FREE_BYTES = 500 * 2**20  # a Quick run writes a few hundred MB before it is slimmed
DISK_LOW = ("Your computer's disk is almost full ({mb:.0f} MB free); a run needs about 500 MB while it works. "
            "Delete old runs under \"My runs\" or free some space on the laptop, then try again.")
DISK_FULL_RUN = ("The run stopped because the computer's disk is full. Delete old runs under \"My runs\" or free "
                 "some space on the laptop, then run it again.")
INTERNAL_ERROR = ("WindLab hit an internal problem during this run. Please run it again; if it happens again, "
                  "restart WindLab (Ctrl+C in its Ubuntu window, then windlab start) and run windlab doctor.")
DIVERGED = ("The airflow calculation became unstable and stopped (the solver \"diverged\"). Try a lower airspeed "
            "or a smaller angle of attack. Also check the part in Step 1: it should be one closed solid, facing "
            "the wind the right way round, with the right units and no extremely thin or sharp details.")


def _free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _disk_full(e: BaseException | None, path: Path) -> bool:
    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
        return True
    free = _free_bytes(path)
    return free is not None and free < 50 * 2**20


def _plain_run_error(e: RunError, case_dir: Path | None = None) -> str:
    """A run that stopped with a problem, in plain words with a next step."""
    if case_dir is not None and _disk_full(None, case_dir):
        return DISK_FULL_RUN
    names = {k: v[0].lower() for k, v in STAGES.items()}
    where = names.get(e.stage, e.stage)
    if e.stage == "setup":
        return ("WindLab could not find the simulation software (OpenFOAM). Follow the OpenFOAM step of the install "
                "guide (docs/INSTALL.md), then run  windlab doctor  to check.")
    if "timed out" in str(e):
        return (f"The run stopped because {where} took far longer than it should. Close other large programs "
                "(for example SolidWorks) and run it again; if it happens again, run  windlab doctor.")
    if e.stage in ("snappyHexMesh", "surfaceFeatureExtract"):
        return ("The mesh could not be fitted around this part. Check in Step 1 that it is one closed solid "
                "(in SolidWorks: File > Save As > STL, one solid body), that the units are right, and that it is "
                "not extremely thin anywhere. Then try again.")
    if e.stage == "checkMesh":
        return ("The mesh around this part failed its quality checks. Check that the part is one closed solid "
                "without extremely thin or sharp details, then try again.")
    if e.stage in ("simpleFoam", "potentialFoam"):
        return DIVERGED
    return (f"The run stopped while {where}. Please run it again; if it happens again, run  windlab doctor  "
            "to check the installation.")


def read_history(case: Path, max_points: int = HISTORY_POINTS) -> list[list[float]]:
    c = results.read_force_coeffs(case)
    if c is None or len(c.get("Time", ())) == 0:
        return []
    it, cl, cd = c["Time"], c["Cl"], c["Cd"]
    step = max(1, int(np.ceil(len(it) / max_points)))
    idx = np.arange(len(it))[::step]
    if idx[-1] != len(it) - 1:
        idx = np.append(idx, len(it) - 1)
    return [[int(it[i]), round(float(cl[i]), 6), round(float(cd[i]), 6)] for i in idx]


def progress_estimate(st: RunState, iteration: int, now: float | None = None) -> dict:
    """Percent done, seconds left and "about N min left" (see :mod:`windlab.app.eta`)."""
    points = getattr(st, "points", None) or []
    finished = [p for p in points if p.get("status") in ("done", "failed")]
    inp = eta.EtaInput(
        status=st.status,
        stage=st.stage,
        now=time.time() if now is None else now,
        started=st.started,
        stage_started=st.stage_started,
        stage_times=dict(st.stage_times or {}),
        iteration=int(iteration or 0),
        converged=st.converged_at is not None,
        max_iterations=int(st.params.get("max_iterations", 1500)),
        n_procs=int(st.params.get("n_procs", 4)),
        n_points=max(1, len(st.params.get("angles") or [0])),
        point_index=int(getattr(st, "point_index", 0) or 0),
        finished_point_iterations=[p.get("iterations") or 0 for p in finished],
        finished_point_seconds=[p.get("seconds") or 0 for p in finished if p.get("seconds")],
    )
    return eta.estimate(inp)


def run_warnings(rep: dict) -> list[str]:
    """Plain-language warnings from a run report."""
    out = []
    if not rep.get("converged", False):
        out.append("The numbers did not fully settle before the iteration limit. Treat them as a rough guide.")
    c = rep.get("coefficients", {})
    if c.get("cl_oscillation", 0) > 0.02:
        out.append("The lift kept swinging up and down (the flow may be unsteady, e.g. stalled). "
                   "Treat the numbers as a rough guide.")
    mesh = rep.get("mesh", {})
    if mesh and not mesh.get("mesh_ok", True):
        n = mesh.get("failed_checks", 0)
        out.append(f"The mesh failed {n} quality check{'s' if n != 1 else ''}; results may be less accurate.")
    for patch, lay in (mesh.get("layers") or {}).items():
        if lay.get("thickness_pct", 100) < 60:
            out.append(f"The thin cell layers along the surface only partly formed on '{patch}' "
                       f"({lay['thickness_pct']:.0f} % of the planned thickness); drag may be less accurate.")
    return out


# A symmetric part's lift at 0 degrees is worth a note only when a student would notice it: at least this
# much on the reference area and at least this share of the drag (task 013: since the thin-feature
# refinement the rocket's spurious lift is a few per cent of its drag, below both).
SYMMETRIC_LIFT_NOTICEABLE = 0.01
SYMMETRIC_LIFT_SHARE = 0.05


def symmetry_notes(params: dict, coefficients: dict, alpha_deg) -> list[str]:
    """An uploaded part that is mirror-symmetric top to bottom has zero lift at 0 degrees; whatever
    the solver shows there is numerical noise from the mesh, and the student should hear that from
    WindLab rather than wonder (task 012, step 3: a 4-fin rocket showed |CL| ~ CD on the Quick mesh;
    task 013 refines the mesh around such fins, so the note only appears when |CL| is still noticeable)."""
    sym = params.get("mirror_symmetric") or {}
    cl, cd = coefficients.get("cl"), coefficients.get("cd")
    if (params.get("geometry_class", "unknown") != "unknown" or not sym.get("z") or alpha_deg is None
            or abs(float(alpha_deg)) > 1e-9 or cl is None or abs(cl) < SYMMETRIC_LIFT_NOTICEABLE
            or (cd and abs(cl) < SYMMETRIC_LIFT_SHARE * abs(cd))):
        return []
    share = f" (about {100 * abs(cl) / abs(cd):.0f} % of the drag)" if cd else ""
    return [f"Your part is mirror-symmetric top to bottom, so at 0° its true lift is zero. The CL of {cl:.3f} shown "
            f"here{share} is numerical noise from the mesh, not physics: thin fins or plates about one mesh cell "
            "thick are not meshed symmetrically and can produce a sideways force. Compare the drag between runs "
            "and ignore this lift; the \"More accurate, slower\" mesh reduces it."]


def thin_notes(params: dict) -> list[str]:
    """The caveats for a run whose thin plates or fins are still rough after the gap refinement, or around
    which the band was left out because of its cell budget (task 014)."""
    tf = params.get("thin_features") or {}
    if not tf.get("rough") and tf.get("extra_levels", 1) != 0:
        return []
    preset = params.get("preset", "quick")
    label = PRESET_LABELS.get(Preset(preset), ("Quick",))[0] if preset in {x.value for x in Preset} else "Quick"
    return thin.notes(tf, label)[1:]


def reynolds_for(airspeed: float, length: float) -> float:
    return reynolds_number(airspeed, length)


__all__ = ["LABEL", "PRESETS", "PRESET_LABELS", "RunManager", "RunRequestError", "STAGES"]
