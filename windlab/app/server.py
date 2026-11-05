"""WindLab web app: FastAPI backend plus the static single-page front end.

    create_app(Settings(...)) -> FastAPI app

Routes (all JSON unless noted):

    GET  /                          the page (static/index.html)
    GET  /api/config                limits, air properties, presets - for the page
    GET  /api/estimate?geometry_id=&n_angles=   expected run time per mesh choice on this computer
    GET  /api/validation-table      the expected-error table (RES-2) with its version and evidence
    GET  /api/glossary              the glossary (UX-1): id, term, aliases, short and longer definition
    GET  /api/lessons               the guided lessons (EDU-1) and whether teacher mode is on
    GET  /api/lessons/{id}          one lesson: objectives, steps (predict / run / observe / explain / question),
                                    the student's latest answers
    POST /api/lessons/{id}/import   {"run": "naca0012-sweep"}  put a pre-computed result into My runs
    POST /api/lessons/{id}/answers  {"step": "q1", "answer": 2}  save an answer locally (questions are graded)
    GET  /api/lessons/{id}/answers  latest answer per step
    POST /api/geometry?units=mm&name=part.stl   body = the STL or STEP file bytes (STEP: units may be left
                                    out to take the file's own; &bodies=all puts every separate solid in)
    POST /api/geometry/naca         {"code": "0012", "chord": 0.3, "span": 0.6}
    POST /api/geometry/builtin      {"shape": "sphere", "diameter": 0.2}  (sphere, cylinder, plate, cube, teardrop)
    GET  /api/geometry/{id}         check report and reference values
    GET  /api/geometry/{id}/preview binary surface (no Cp), see windlab.app.surface
    GET  /api/geometry/{id}/section mid-span outline (x, z loops) for the instant 2D preview
    POST /api/geometry/{id}/orient  {"rotate": "x" | "y" | "z" | "suggest"}  (uploads; 90° turns)
    POST /api/geometry/{id}/reference  {"length": m | null, "area": m² | null, "part_type": "wing" | "body" |
                                    "auto"}  (null = default; part_type: an upload's convention, frontal or planform)
    POST /api/runs                  {"geometry_id", "airspeed", "alpha_deg", "preset"}  or a sweep:
                                    {"kind": "sweep", "geometry_id", "airspeed", "angles": [-4, 0, 4]}
                                    (needs the header X-WindLab-Disclaimer: <version> from /api/config, which
                                    the page sends once the educational-use notice has been accepted; else 403)
    GET  /api/runs/{id}/results.csv coefficients (one row per angle), units in the header
    GET  /api/runs/{id}/history.csv CL/CD against iteration (?point=i for a sweep angle)
    GET  /api/runs/{id}/case.zip    the finished run as a self-contained OpenFOAM case (RUN-3)
    GET  /api/runs/{id}/report.md   the finished run as a self-contained Markdown report (RES-4)
    GET  /api/runs/{id}/report.html the same as a printable page (print to PDF in the browser)
    GET  /api/runs                  run history (newest first): shape, speed, angle, CL, CD, status
    GET  /api/compare?runs=id1,id2  2-5 finished runs side by side, with comparability warnings
    GET  /api/compare.csv?runs=...  the same as a CSV table
    GET  /api/runs/{id}             status, stage, percent, CL/CD history, results, warnings
    POST /api/runs/{id}/cancel      stop a queued or running run
    DELETE /api/runs/{id}           delete a finished run and its case folder
    GET  /api/runs/{id}/surface     binary surface with Cp (?format=json for debugging, ?point=i for sweeps)
    GET  /api/runs/{id}/slice       mid-span flow slice: |U|/U_inf and Cp at points (windlab.app.slice)
    POST /api/feedback              {"results_sensible": 1-5, "easy_to_use": 1-5, "comment", "name", "run_id"}
                                    (stored in feedback.jsonl in the data folder; nothing is sent anywhere)
    GET  /api/runs/{id}/slice-info  close-up / whole-tunnel windows, body box, streamlines (JSON)

Never accepts OpenFOAM dictionaries or scripts; every case file comes from a template
with numbers computed and clamped by windlab.case_builder.
"""

from __future__ import annotations

import errno
import os
import tempfile
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from windlab import __version__, disclaimer, expected_error, feedback, glossary, lessons
from windlab.app import case_export, housekeeping, report, surface
from windlab.app import slice as flow_slice
from windlab.app.geometry_store import (
    CHORD_RANGE,
    MAX_ASPECT_RATIO,
    SPAN_RANGE,
    TAPER_RANGE,
    GeometryError,
    GeometryStore,
    parse_uuid,
)
from windlab.app.runs import LABEL, MAX_COMPARE, STAGES, RunConflictError, RunManager, RunRequestError
from windlab.app.security import GuardMiddleware
from windlab.case_builder import DEFAULT_SWEEP_ANGLES, MAX_SWEEP_ANGLES, NU_SEA_LEVEL, RHO_SEA_LEVEL
from windlab.geometry import section, shapes, step
from windlab.geometry.upload import MAX_FILE_BYTES, UNIT_NAMES

STATIC = Path(__file__).resolve().parent / "static"

# request fields in the words the page uses (for "Some values are missing or not valid: ...")
FIELD_NAMES = {
    "airspeed": "the airspeed", "alpha_deg": "the angle of attack", "angles": "the angles of attack",
    "geometry_id": "the shape", "code": "the NACA code", "chord": "the chord", "span": "the span",
    "taper": "the taper", "units": "the units", "rotate": "the turn", "length": "the reference length",
    "area": "the reference area", "kind": "the kind of run", "preset": "the mesh", "point": "the angle",
    "runs": "the runs to compare", "results_sensible": "the rating \"the results made sense\"",
    "easy_to_use": "the rating \"easy to use\"", "comment": "the comment (at most 2000 characters)",
    "name": "the name (at most 80 characters)", "run_id": "the run",
}

# The report page is a standalone document: its own <style> and data: images, still no scripts and nothing
# from the network (the middleware keeps this policy for that response instead of the page's stricter one).
REPORT_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; object-src 'none'; base-uri 'none'; "
              "form-action 'none'; frame-ancestors 'none'")

DISK_FULL = ("The computer's disk is full, so WindLab could not save its files. Delete old runs under "
             "\"My runs\" or free some space on the laptop, then try again.")
UNEXPECTED = ("WindLab hit an unexpected problem. Please try again. If it keeps happening, stop WindLab "
              "(Ctrl+C in its Ubuntu window), start it again with  windlab start  and reload this page.")


def unexpected_error_message(exc: BaseException) -> tuple[int, str]:
    """(HTTP status, plain message) for an exception no route handled."""
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return 507, DISK_FULL
    return 500, UNEXPECTED


@dataclass
class Settings:
    data_dir: Path
    n_procs: int = 4
    allowed_hosts: list[str] = field(default_factory=list)
    max_upload_bytes: int = MAX_FILE_BYTES
    max_json_bytes: int = 64 * 1024
    runner: object | None = None  # tests inject a fake solver here
    export_cap_bytes: int = case_export.DEFAULT_SIZE_CAP
    lesson: str | None = None  # teacher mode (EDU-2): open straight into this lesson, hide advanced controls
    keep_runs: int = housekeeping.DEFAULT_KEEP_RUNS
    disk_cap_bytes: int = housekeeping.DEFAULT_DISK_CAP_BYTES
    geometry_max_age_days: float = housekeeping.DEFAULT_GEOMETRY_MAX_AGE_DAYS


class NacaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=4)
    chord: float = Field(allow_inf_nan=False)
    span: float = Field(allow_inf_nan=False)
    taper: float = Field(default=1.0, allow_inf_nan=False)


class BuiltinRequest(BaseModel):
    """A built-in body: the shape id plus its one or two sizes (unknown keys are refused by the generator)."""
    model_config = ConfigDict(extra="allow")
    shape: str = Field(max_length=20, pattern="^[a-z]+$")


class OrientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rotate: str = Field(pattern="^(x|y|z|suggest)$")


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    length: float | None = Field(default=None, allow_inf_nan=False)
    area: float | None = Field(default=None, allow_inf_nan=False)
    # uploads: which convention the defaults follow (a body's frontal area, a wing's planform area)
    part_type: str | None = Field(default=None, pattern="^(wing|body|auto)$")


class LessonImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: str = Field(max_length=40, pattern="^[a-z0-9-]+$")


class LessonAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: str = Field(max_length=40, pattern="^[a-z0-9-]+$")
    answer: str | int = Field(union_mode="left_to_right")


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results_sensible: int | None = Field(default=None, ge=1, le=5)
    easy_to_use: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=feedback.MAX_COMMENT)
    name: str = Field(default="", max_length=feedback.MAX_NAME)
    run_id: str | None = Field(default=None, max_length=36)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry_id: str = Field(max_length=36)
    airspeed: float = Field(allow_inf_nan=False)
    alpha_deg: float | None = Field(default=None, allow_inf_nan=False)
    kind: str = Field(default="single", pattern="^(single|sweep)$")
    angles: list[Annotated[float, Field(allow_inf_nan=False)]] | None = Field(default=None, max_length=20)
    preset: str = Field(default="quick", max_length=16)
    units_confirmed: bool = False


def create_app(settings: Settings) -> FastAPI:
    data = Path(settings.data_dir)
    data_dir_ = data
    if settings.lesson is not None:
        try:
            lessons.get_lesson(settings.lesson)
        except (KeyError, lessons.LessonError):
            raise ValueError(f"unknown lesson {settings.lesson!r}; known: "
                             + ", ".join(x["id"] for x in lessons.list_lessons())) from None
    geometry = GeometryStore(data / "geometry")
    kwargs = {"runner": settings.runner} if settings.runner is not None else {}
    uploads_tmp = data / "incoming"
    uploads_tmp.mkdir(parents=True, exist_ok=True)
    runs = RunManager(data / "runs", geometry, n_procs=settings.n_procs, data_root=data,
                      keep_runs=settings.keep_runs, disk_cap_bytes=settings.disk_cap_bytes,
                      geometry_max_age_days=settings.geometry_max_age_days, incoming=uploads_tmp, **kwargs)
    runs.housekeep()  # at start-up: old runs, unused shapes, stale uploads

    @asynccontextmanager
    async def lifespan(_app):
        yield
        runs.stop(timeout=1.0)

    app = FastAPI(title="WindLab", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.geometry, app.state.runs, app.state.settings = geometry, runs, settings

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # name the field in plain words, not a list index (("body", "angles", 0) -> "the angles of attack")
        fields = sorted({FIELD_NAMES.get(f, f) for f in (
            next((str(x) for x in reversed(e["loc"]) if isinstance(x, str) and x != "body"), str(e["loc"][-1]))
            for e in exc.errors() if e.get("loc"))})
        msg = "Some values are missing or not valid"
        msg += f": {', '.join(fields)}." if fields else "."
        return JSONResponse({"detail": msg + " Please check them and try again."}, status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        # never a stack trace in the browser: a plain message with a next step
        status, msg = unexpected_error_message(exc)
        return JSONResponse({"detail": msg}, status_code=status)

    # ------------------------------------------------------------------ page

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/disclaimer", include_in_schema=False)
    def disclaimer_text():
        """The full educational-use notice (DISCLAIMER.md) as plain text, linked from the page and the About panel."""
        return Response(disclaimer.FULL_TEXT, media_type="text/plain; charset=utf-8")

    app.mount("/static", StaticFiles(directory=STATIC, follow_symlink=False, check_dir=False), name="static")

    @app.get("/api/config")
    def config():
        return {
            "version": __version__,
            "commit": case_export.windlab_commit(),
            "label": LABEL,
            # for the "About WindLab" panel: third-party components and their licences, and the OpenFOAM notice
            "third_party": [{"name": n, "licence": lic, "what": what} for n, lic, what in report.THIRD_PARTY],
            "trademark": {"acknowledgement": case_export.TRADEMARK_ACKNOWLEDGEMENT,
                          "disclaimer": case_export.TRADEMARK_DISCLAIMER, "gpl": case_export.GPL_NOTICE},
            # the educational-use notice the page must have accepted (its version, the checkbox text, the header
            # every run request must carry): windlab.disclaimer
            "disclaimer": disclaimer.config_view(),
            "airspeed": {"min": 1.0, "max": 100.0, "default": 20.0, "unit": "m/s"},
            "alpha_deg": {"min": -20.0, "max": 20.0, "default": 5.0, "unit": "deg"},
            "air": {"nu": NU_SEA_LEVEL, "rho": RHO_SEA_LEVEL, "description": "sea-level air at 15 °C"},
            "naca": {"chord": CHORD_RANGE, "span": SPAN_RANGE, "taper": TAPER_RANGE,
                     "max_aspect_ratio": MAX_ASPECT_RATIO},
            "builtin_shapes": shapes.catalogue(),
            "units": UNIT_NAMES,
            "max_upload_mb": settings.max_upload_bytes / 2**20,
            "step_import": {"available": step.available(), "message": None if step.available() else step.UNAVAILABLE},
            "presets": runs.preset_choices(),
            "n_procs": settings.n_procs,
            "stages": {k: v[0] for k, v in STAGES.items()},
            "sweep": {"default_angles": list(DEFAULT_SWEEP_ANGLES), "max_angles": MAX_SWEEP_ANGLES},
            "lesson": settings.lesson,  # teacher mode: the page opens this lesson and hides advanced controls
        }

    @app.get("/api/estimate")
    def estimate(geometry_id: str | None = Query(default=None, max_length=36),
                 n_angles: int = Query(default=1, ge=1, le=MAX_SWEEP_ANGLES)):
        """Expected run time per mesh choice on this computer (windlab.speed), shown before a run."""
        return runs.estimates(geometry_id, n_angles)

    @app.get("/api/validation-table")
    def validation_table():
        """The versioned expected-error table the ranges next to CL and CD come from (PRD RES-2)."""
        return expected_error.load_table()

    @app.get("/api/glossary")
    def get_glossary():
        """Every term's definition (PRD UX-1): the page's (?) buttons and Glossary panel read this."""
        return glossary.load()

    # ------------------------------------------------------------------ lessons (EDU-1 / EDU-2)

    @app.get("/api/lessons")
    def list_lessons():
        return {"lessons": lessons.list_lessons(), "teacher_mode": settings.lesson is not None,
                "lesson": settings.lesson}

    @app.get("/api/lessons/{lesson_id}")
    def get_lesson(lesson_id: str):
        with _lesson_errors():
            data = lessons.get_lesson(lesson_id)
            data["answers"] = lessons.latest_answers(data_dir_, data["id"])
            return data

    @app.post("/api/lessons/{lesson_id}/import", status_code=201)
    async def import_lesson_run(lesson_id: str, req: LessonImportRequest):
        """Bring a pre-computed result into My runs (a finished run); returns its status."""
        with _lesson_errors():
            lesson = lessons.get_lesson(lesson_id)
        step = next((s for s in lesson["steps"] if s["kind"] == "run" and s.get("pack") == req.run), None)
        if step is None:
            raise HTTPException(404, "That pre-computed result is not part of this lesson.")
        try:
            st = await run_in_threadpool(runs.import_pack, lesson["id"], req.run, step)
        except RunRequestError as e:
            raise HTTPException(400, str(e)) from None
        except (KeyError, lessons.LessonError):
            raise HTTPException(404, "The pre-computed result for this step is not installed.") from None
        return runs.status(st.run_id)

    @app.post("/api/lessons/{lesson_id}/answers", status_code=201)
    def post_lesson_answer(lesson_id: str, req: LessonAnswerRequest):
        with _lesson_errors():
            lesson = lessons.get_lesson(lesson_id)
        step = next((s for s in lesson["steps"] if s["id"] == req.step), None)
        if step is None:
            raise HTTPException(404, "That lesson step was not found.")
        correct = None
        if step["kind"] == "question":
            if not isinstance(req.answer, int) or not 0 <= req.answer < len(step["choices"]):
                raise HTTPException(400, "Please choose one of the answers.")
            correct = req.answer == step["answer"]
        try:
            rec = lessons.add_answer(data_dir_, lesson["id"], req.step, req.answer, correct)
        except lessons.LessonError as e:
            raise HTTPException(400, str(e)) from None
        out = {"saved": True, "step": req.step, "correct": correct}
        if step["kind"] == "question":
            out["explanation"] = step.get("explanation", "")
            out["answer"] = step["answer"]
        out["time"] = rec["time"]
        return out

    @app.get("/api/lessons/{lesson_id}/answers")
    def get_lesson_answers(lesson_id: str):
        with _lesson_errors():
            lessons.get_lesson(lesson_id)
            return {"answers": lessons.latest_answers(data_dir_, lesson_id)}

    # ------------------------------------------------------------------ geometry

    @app.post("/api/geometry")
    async def upload_geometry(request: Request, units: str | None = Query(default=None, max_length=4),
                              name: str | None = Query(default=None, max_length=200),
                              bodies: str = Query(default="largest", pattern="^(largest|all)$")):
        """An STL file (units required) or a STEP file (units optional: the file's own are read)."""
        if units in ("", "file", "auto"):
            units = None
        if units is not None and units not in UNIT_NAMES:
            raise HTTPException(400, "Please choose the units the part was drawn in: mm, cm, m or in.")
        fd, tmp = tempfile.mkstemp(prefix="upload-", suffix=".bin", dir=uploads_tmp)
        tmp_path = Path(tmp)
        try:
            size = 0
            with os.fdopen(fd, "wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(
                            413, f"The file is too large; the limit is {settings.max_upload_bytes / 2**20:.0f} MB."
                        )
                    fh.write(chunk)
            if size == 0:
                raise HTTPException(400, "No file was received. Please choose an STL or STEP file.")
            try:
                meta = await run_in_threadpool(geometry.add_upload, tmp_path, units, name, bodies)
            except GeometryError as e:
                runs.usage.event("upload_failed", message=str(e))
                raise HTTPException(400, str(e)) from None
        finally:
            tmp_path.unlink(missing_ok=True)
        runs.usage.event("upload" if meta.get("usable") else "upload_failed",
                         message=None if meta.get("usable") else "the check report found problems with the part")
        return _geometry_view(meta, runs)

    @app.post("/api/geometry/naca")
    async def naca(req: NacaRequest):
        try:
            meta = await run_in_threadpool(geometry.add_naca, req.code, req.chord, req.span, req.taper)
        except GeometryError as e:
            raise HTTPException(400, str(e)) from None
        runs.usage.event("naca")
        return _geometry_view(meta, runs)

    @app.post("/api/geometry/builtin")
    async def builtin(req: BuiltinRequest):
        params = {k: v for k, v in req.model_dump().items() if k != "shape"}
        if len(params) > 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in params.values()):
            raise HTTPException(400, "Please give the shape's sizes as numbers.")
        try:
            meta = await run_in_threadpool(geometry.add_builtin, req.shape, **params)
        except GeometryError as e:
            raise HTTPException(400, str(e)) from None
        runs.usage.event("naca")  # counted with the built-in wings: "built-in shapes made"
        return _geometry_view(meta, runs)

    @app.get("/api/geometry/{geometry_id}")
    def get_geometry(geometry_id: str):
        return _geometry_view(_load_geometry(geometry, geometry_id), runs)

    @app.post("/api/geometry/{geometry_id}/orient")
    async def orient_geometry(geometry_id: str, req: OrientRequest):
        _load_geometry(geometry, geometry_id)
        try:
            meta, note = await run_in_threadpool(geometry.orient, geometry_id, req.rotate)
        except GeometryError as e:
            raise HTTPException(400, str(e)) from None
        except (KeyError, ValueError):
            raise HTTPException(400, "This shape could not be turned. Please upload it again.") from None
        return {**_geometry_view(meta, runs), "note": note}

    @app.post("/api/geometry/{geometry_id}/reference")
    def set_reference(geometry_id: str, req: ReferenceRequest):
        _load_geometry(geometry, geometry_id)
        try:
            meta = geometry.set_reference(geometry_id, req.length, req.area, req.part_type)
        except GeometryError as e:
            raise HTTPException(400, str(e)) from None
        return _geometry_view(meta, runs)

    @app.get("/api/geometry/{geometry_id}/preview")
    def geometry_preview(geometry_id: str):
        _load_geometry(geometry, geometry_id)
        try:
            blob = surface.stl_surface(geometry.stl_path(geometry_id))
        except (KeyError, surface.SurfaceError):
            raise HTTPException(404, "This shape has no surface to show.") from None
        return Response(blob, media_type="application/octet-stream")

    @app.get("/api/geometry/{geometry_id}/section")
    async def geometry_section(geometry_id: str):
        """The outline of the part at mid-span, for the instant browser preview (PRD Tier 0)."""
        meta = _load_geometry(geometry, geometry_id)
        if not meta.get("usable"):
            raise HTTPException(400, "Fix the problems with the shape first.")
        try:
            path = None if meta.get("kind") == "naca" else geometry.stl_path(geometry_id)
            return await run_in_threadpool(section.section_for_geometry, meta, path)
        except KeyError:
            raise HTTPException(404, "This shape has no geometry to cut.") from None
        except section.SectionError as e:
            raise HTTPException(400, str(e)) from None

    # ------------------------------------------------------------------ runs

    @app.post("/api/runs", status_code=202)
    async def create_run(req: RunRequest, request: Request):
        # the page adds X-WindLab-Disclaimer: <version> once the notice has been accepted (windlab.disclaimer)
        if not disclaimer.request_accepted(request.headers):
            raise HTTPException(403, disclaimer.REJECTED)
        try:
            if req.kind == "sweep":
                if req.angles is None:
                    raise RunRequestError("Please give the angles for the sweep.")
                st = await run_in_threadpool(runs.submit_sweep, req.geometry_id, req.airspeed, req.angles,
                                             req.preset, req.units_confirmed)
            else:
                if req.alpha_deg is None:
                    raise RunRequestError("Please give the angle of attack.")
                st = await run_in_threadpool(runs.submit, req.geometry_id, req.airspeed, req.alpha_deg, req.preset,
                                             req.units_confirmed)
        except RunRequestError as e:
            raise HTTPException(400, str(e)) from None
        return runs.status(st.run_id)

    @app.get("/api/runs")
    def list_runs():
        return {"runs": runs.list_runs(), "keep_runs": settings.keep_runs,
                "disk_cap_gb": round(settings.disk_cap_bytes / 2**30, 1)}

    @app.get("/api/compare")
    def compare_runs(runs_: str = Query(alias="runs", max_length=MAX_COMPARE * 40)):
        with _run_errors(not_found="One of the runs to compare was not found (it may have been deleted)."):
            return runs.compare(runs_)

    @app.get("/api/compare.csv")
    def compare_runs_csv(runs_: str = Query(alias="runs", max_length=MAX_COMPARE * 40)):
        with _run_errors(not_found="One of the runs to compare was not found (it may have been deleted)."):
            name, text = runs.compare_csv(runs_)
        return _csv_response(name, text)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, point: int | None = Query(default=None, ge=0, le=99)):
        with _run_errors():
            return runs.status(run_id, point)

    @app.get("/api/runs/{run_id}/results.csv")
    def results_csv(run_id: str):
        with _run_errors():
            name, text = runs.results_csv(run_id)
        return _csv_response(name, text)

    @app.get("/api/runs/{run_id}/history.csv")
    def history_csv(run_id: str, point: int | None = Query(default=None, ge=0, le=99)):
        with _run_errors():
            name, text = runs.history_csv(run_id, point)
        return _csv_response(name, text)

    @app.get("/api/runs/{run_id}/case.zip")
    async def case_zip(run_id: str):
        """The finished run as a self-contained OpenFOAM case (RUN-3)."""
        with _run_errors():
            st = runs.get(run_id)
            if st.status != "done":
                raise RunConflictError("The OpenFOAM case can be downloaded once the run has finished.")
        try:
            blob = await run_in_threadpool(case_export.export_case, st.case_dir, st.to_json(),
                                           settings.export_cap_bytes)
        except case_export.ExportError as e:
            raise HTTPException(409, str(e)) from None
        # name is built from a date and a UUID only
        return Response(blob, media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="{case_export.zip_name(st.to_json())}"'})

    @app.get("/api/runs/{run_id}/report.md")
    async def report_markdown(run_id: str):
        """The finished run as a self-contained Markdown report (RES-4)."""
        text, name = await run_in_threadpool(_report, run_id, "md")
        return Response(text, media_type="text/markdown; charset=utf-8",
                        headers={"content-disposition": f'attachment; filename="{name}"'})

    @app.get("/api/runs/{run_id}/report.html")
    async def report_html(run_id: str):
        """The same report as a printable page (opens in the browser; print to PDF). Inline styles and
        data: images are what make it self-contained, so this response carries its own policy."""
        text, name = await run_in_threadpool(_report, run_id, "html")
        return Response(text, media_type="text/html; charset=utf-8",
                        headers={"content-security-policy": REPORT_CSP,
                                 "content-disposition": f'inline; filename="{name}"'})

    def _report(run_id: str, ext: str) -> tuple[str, str]:
        with _run_errors():
            st = runs.get(run_id)
            if st.status != "done" or not st.result:
                raise RunConflictError("The report can be downloaded once the run has finished.")
            status = runs.status(run_id)
        try:
            gmeta = geometry.get(st.geometry_id)
        except (KeyError, GeometryError, OSError, ValueError):
            gmeta = None
        ctx = report.collect(st, status, gmeta, st.case_dir)
        text = report.markdown(ctx) if ext == "md" else report.html_page(ctx)
        return text, report.file_name(status, ext)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        with _run_errors():
            runs.cancel(run_id)
            return runs.status(run_id)

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str):
        with _run_errors():
            runs.delete(run_id)
        return {"deleted": run_id}

    @app.get("/api/runs/{run_id}/surface")
    def get_surface(run_id: str, format: str = Query(default="binary", pattern="^(binary|json)$"),
                    point: int | None = Query(default=None, ge=0, le=99)):
        try:
            blob = runs.surface_blob(run_id, point)
        except RunRequestError as e:
            raise HTTPException(400, str(e)) from None
        except KeyError:
            raise HTTPException(404, "There is no pressure picture for this run (yet).") from None
        if format == "json":
            d = surface.decode(blob)
            return {
                "vertices": d["vertices"].round(5).tolist(),
                "triangles": d["triangles"].tolist(),
                "cp": None if d["cp"] is None else d["cp"].round(4).tolist(),
                "cp_min": d["cp_min"],
                "cp_max": d["cp_max"],
            }
        return Response(blob, media_type="application/octet-stream")

    @app.get("/api/runs/{run_id}/slice")
    def get_slice(run_id: str, format: str = Query(default="binary", pattern="^(binary|json)$"),
                  point: int | None = Query(default=None, ge=0, le=99)):
        try:
            blob = runs.surface_blob(run_id, point, what="slice")
        except RunRequestError as e:
            raise HTTPException(400, str(e)) from None
        except KeyError:
            raise HTTPException(404, "There is no flow slice for this run (yet).") from None
        if format == "json":
            d = flow_slice.decode(blob)
            return {"xz": d["xz"].round(5).tolist(), "triangles": d["triangles"].tolist(),
                    "speed": d["speed"].round(4).tolist(), "cp": d["cp"].round(4).tolist(),
                    **{k: d[k] for k in ("x_min", "x_max", "z_min", "z_max", "speed_max", "cp_min", "cp_max", "y")}}
        return Response(blob, media_type="application/octet-stream")

    # ------------------------------------------------------------------ trial feedback

    @app.post("/api/feedback", status_code=201)
    def post_feedback(req: FeedbackRequest):
        run_id = None
        if req.run_id:
            try:
                run_id = parse_uuid(req.run_id)
            except GeometryError:
                run_id = None  # not worth refusing the feedback for
        try:
            feedback.add_feedback(data, req.results_sensible, req.easy_to_use, req.comment, req.name, run_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"saved": True, "message": "Thank you! Your answer is saved on this computer only. To send it to "
                                          "your instructor, run  windlab feedback export  and e-mail the file."}

    @app.get("/api/runs/{run_id}/slice-info")
    def get_slice_info(run_id: str, point: int | None = Query(default=None, ge=0, le=99)):
        try:
            return runs.slice_info(run_id, point)
        except RunRequestError as e:
            raise HTTPException(400, str(e)) from None
        except (KeyError, OSError, ValueError, surface.SurfaceError):
            raise HTTPException(404, "There is no flow slice for this run (yet).") from None

    guard = GuardMiddleware(
        app,
        allowed_hosts=settings.allowed_hosts,
        max_body=settings.max_json_bytes,
        max_upload=settings.max_upload_bytes + 64 * 1024,
        upload_paths=("/api/geometry",),
    )
    guard.fastapi = app  # for tests and the CLI
    return guard


def _csv_response(name: str, text: str) -> Response:
    # name is built from a date and a UUID only (see runs.csv_name)
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{name}"'})


@contextmanager
def _lesson_errors():
    try:
        yield
    except lessons.LessonError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "That lesson was not found.") from None


@contextmanager
def _run_errors(not_found: str = "That run was not found. It may have been deleted; see the list under "
                                 "\"My runs\"."):
    """Run-manager exceptions -> plain-language HTTP errors."""
    try:
        yield
    except RunConflictError as e:
        raise HTTPException(409, str(e)) from None
    except RunRequestError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, not_found) from None


def _load_geometry(store: GeometryStore, geometry_id: str) -> dict:
    try:
        return store.get(geometry_id)
    except GeometryError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "That shape was not found.") from None


def _part_type_view(meta: dict) -> dict | None:
    """Which convention the reference area follows: wing (planform) or body (frontal), guessed or chosen."""
    if meta.get("kind") == "naca":
        return {"detected": "wing", "chosen": None, "used": "wing"}
    if meta.get("kind") == "builtin":
        return {"detected": "body", "chosen": None, "used": "body"}
    pt = meta.get("part_type")
    if not pt:
        return None
    return {**pt, "used": pt.get("chosen") or pt.get("detected")}


def _geometry_view(meta: dict, runs: RunManager) -> dict:
    r = meta["report"]
    return {
        # uploads: per offered preset, the thin plates / fins found at that mesh's cell size and the notes the
        # page shows before a run (windlab.geometry.thin); {} when nothing is thin
        "thin_features": runs.thin_features_for(meta),
        "geometry_id": meta["id"],
        "kind": meta["kind"],
        "class": meta.get("class") or ("streamlined-lifting" if meta["kind"] == "naca" else "unknown"),
        "name": meta["name"],
        "usable": meta["usable"],
        "units": r["units"],
        "size_m": r["size_m"],
        "longest_m": r["longest_m"],
        "size_suspicious": r["size_suspicious"],
        "suggested_units": r["suggested_units"],
        "triangles": r["triangles"],
        "pieces": r["pieces"],
        "messages": r["messages"],
        "problems": r["problems"],
        "warnings": r["warnings"],
        "reference": meta.get("reference"),
        "reference_default": meta.get("reference_default") or meta.get("reference"),
        "part_type": _part_type_view(meta),
        "areas": meta.get("areas"),  # uploads: both silhouette areas {"frontal", "planform", "method"}
        "orientation": meta.get("orientation") or [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "naca": meta.get("naca"),
        "builtin": meta.get("builtin"),
        "description": meta.get("description"),
        "format": r.get("format"),
        "source": r.get("source"),  # STEP facts (declared unit, solids, fusing, healing), else None
    }
