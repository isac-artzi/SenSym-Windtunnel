"""Guided lessons (PRD EDU-1/EDU-2): content, pre-computed result packs, local answers.

Layout (package data, so a plain ``pip install .`` ships it)::

    windlab/lessons/<id>/lesson.json      the structured lesson the app walks through
    windlab/lessons/<id>/lesson.md        background reading for the student (printable)
    windlab/lessons/<id>/pack/<run>/      a pre-computed Quick result: run.json, history.json,
                                          surface*.bin.gz, slice*.bin.gz, sliceinfo*.json.gz

A pack run is **imported** into "My runs" on request (``import_pack_run``): the case folder
is rebuilt from WindLab's own template with the lesson's inputs (so the OpenFOAM case
export works), the stored pictures and histories are put where a real run leaves them,
and the status is written as a finished run whose params carry ``source = "lesson pack"``.
Nothing in a pack is executed; every file is checked and copied by name.

Answers (predictions, observations, comprehension questions) are stored on the student's
computer in ``lesson_answers.jsonl`` and go into ``windlab feedback export``.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

from windlab import __version__, results
from windlab.app import slice as flow_slice
from windlab.app import surface
from windlab.app.runs import read_history
from windlab.feedback import clean_text

LESSON_ROOT = Path(__file__).resolve().parent
PACK_VERSION = 1
PACK_MAX_TRIANGLES = 12_000  # pictures are decimated for the repository (about 100 KB per picture)
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STEP_KINDS = ("intro", "predict", "run", "observe", "explain", "question", "homework")
ANSWERS_FILE = "lesson_answers.jsonl"
MAX_ANSWER = 2000


class LessonError(ValueError):
    """Plain-language problem with a lesson request."""


# --------------------------------------------------------------------------- content


def lesson_dirs() -> list[Path]:
    return sorted(d for d in LESSON_ROOT.iterdir() if d.is_dir() and (d / "lesson.json").is_file())


@lru_cache(maxsize=1)
def _all() -> dict[str, dict]:
    out = {}
    for d in lesson_dirs():
        data = json.loads((d / "lesson.json").read_text())
        check_lesson(data, d)
        out[data["id"]] = data
    return out


def list_lessons() -> list[dict]:
    """Short cards for the Lessons page, in the order given by ``order``."""
    items = sorted(_all().values(), key=lambda d: (d.get("order", 99), d["id"]))
    return [{"id": d["id"], "title": d["title"], "minutes": d.get("minutes", 50), "summary": d.get("summary", ""),
             "objectives": d.get("objectives", []), "runs": [s["pack"] for s in d["steps"] if s["kind"] == "run"],
             "pack_available": all(pack_dir(d["id"], s["pack"]).is_dir() for s in d["steps"]
                                   if s["kind"] == "run" and s.get("pack"))}
            for d in items]


def get_lesson(lesson_id: str) -> dict:
    lid = parse_id(lesson_id)
    data = _all().get(lid)
    if data is None:
        raise KeyError(lesson_id)
    out = json.loads(json.dumps(data))  # a copy the caller may change
    for s in out["steps"]:
        if s["kind"] == "run":
            s["pack_available"] = pack_dir(lid, s["pack"]).is_dir() if s.get("pack") else False
    md = LESSON_ROOT / lid / "lesson.md"
    out["markdown"] = md.read_text() if md.is_file() else ""
    return out


def parse_id(value: str) -> str:
    v = str(value)
    if not _ID.match(v) or len(v) > 40:
        raise LessonError("That lesson was not found.")
    return v


def check_lesson(data: dict, where: Path | None = None) -> None:
    """A lesson has an id, title, 2-3 objectives, steps of known kinds with unique ids,
    at least one prediction before the first run, three questions with a correct answer."""
    tag = f"{where}: " if where else ""
    if not _ID.match(data.get("id", "")) or (where is not None and data["id"] != where.name):
        raise ValueError(f"{tag}bad lesson id")
    if not data.get("title") or not 2 <= len(data.get("objectives", [])) <= 3:
        raise ValueError(f"{tag}needs a title and 2-3 objectives")
    ids, kinds = set(), []
    for s in data.get("steps", []):
        if s.get("kind") not in STEP_KINDS:
            raise ValueError(f"{tag}unknown step kind {s.get('kind')!r}")
        if not _ID.match(s.get("id", "")) or s["id"] in ids:
            raise ValueError(f"{tag}step ids must be unique kebab-case ({s.get('id')!r})")
        ids.add(s["id"])
        kinds.append(s["kind"])
        if s["kind"] == "run" and (not _NAME.match(s.get("pack", "")) or "shape" not in s or "conditions" not in s):
            raise ValueError(f"{tag}run step {s['id']} needs pack, shape and conditions")
        if s["kind"] == "question":
            ch = s.get("choices", [])
            if len(ch) < 2 or not isinstance(s.get("answer"), int) or not 0 <= s["answer"] < len(ch):
                raise ValueError(f"{tag}question {s['id']} needs choices and the index of the right one")
    if kinds.count("question") != 3:
        raise ValueError(f"{tag}a lesson has exactly 3 comprehension questions")
    if "run" in kinds and "predict" not in kinds[: kinds.index("run")]:
        raise ValueError(f"{tag}the student predicts before the first run")


# --------------------------------------------------------------------------- packs


def pack_dir(lesson_id: str, name: str) -> Path:
    lid = parse_id(lesson_id)
    if not _NAME.match(name or "") or len(name) > 40:
        raise LessonError("That pre-computed result was not found.")
    return LESSON_ROOT / lid / "pack" / name


def _gz_write(path: Path, data: bytes) -> None:
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(data)


def _gz_read(path: Path, limit: int = 64 * 2**20) -> bytes:
    with gzip.open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise LessonError("A pre-computed result file is unexpectedly large.")
    return data


def _shrink_surface(blob: bytes, max_triangles: int = PACK_MAX_TRIANGLES) -> bytes:
    d = surface.decode(blob)
    v, t, cp = surface.decimate(d["vertices"], d["triangles"], d["cp"], max_triangles)
    return surface.encode(v, t, cp)


def _shrink_slice(blob: bytes, max_triangles: int = PACK_MAX_TRIANGLES) -> bytes:
    d = flow_slice.decode(blob)
    pts = np.column_stack([d["xz"][:, 0], np.full(len(d["xz"]), d["y"]), d["xz"][:, 1]])
    vals = np.column_stack([d["speed"], d["cp"]])
    pts, t, vals = surface.decimate(pts, d["triangles"], vals, max_triangles)
    return flow_slice.encode(pts, t, vals[:, 0], vals[:, 1])


def _shrink_sliceinfo(text: str) -> str:
    """Keep the windows and body box; thin the streamlines to every other point."""
    info = json.loads(text)
    lines = info.get("streamlines")
    if lines:
        info["streamlines"] = {k: [[[round(x, 4), round(z, 4)] for x, z in line[::2]] for line in v]
                               for k, v in lines.items()}
    for k in ("body_min", "body_max"):
        if k in info:
            info[k] = [round(float(x), 5) for x in info[k]]
    return json.dumps(info, separators=(",", ":"))


def pack_from_run(case_dir: Path, out_dir: Path, name: str) -> int:
    """Write a compact pack of a finished run; returns its size in bytes."""
    case = Path(case_dir)
    st = json.loads((case / "windlab_status.json").read_text())
    if st.get("status") != "done" or not st.get("result"):
        raise LessonError("only finished runs can be packed")
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    meta = json.loads((case / "windlab_case.json").read_text())
    p = st["params"]
    sweep = p.get("kind") == "sweep"
    points = range(len(st["result"].get("points") or [])) if sweep else [None]
    history: dict[str, list] = {}
    for i in points:
        suffix = "" if i is None else f"_{i}"
        src = case if i is None else case / "sweep" / f"p{i}"
        history[str(i if i is not None else 0)] = read_history(src)
        for what, shrink in (("surface", _shrink_surface), ("slice", _shrink_slice)):
            f = case / f"windlab_{what}{suffix}.bin"
            if f.is_file():
                _gz_write(out / f"{what}{suffix}.bin.gz", shrink(f.read_bytes()))
        info = case / f"windlab_sliceinfo{suffix}.json"
        if info.is_file():
            _gz_write(out / f"sliceinfo{suffix}.json.gz", _shrink_sliceinfo(info.read_text()).encode())
    run = {
        "pack_version": PACK_VERSION,
        "name": name,
        "windlab_version": __version__,
        "computed": time.strftime("%Y-%m-%d", time.localtime(st.get("finished") or time.time())),
        "params": {k: p.get(k) for k in ("kind", "airspeed", "alpha_deg", "angles", "preset", "reynolds", "mach",
                                          "reference_area", "reference_length", "reference_how", "max_iterations",
                                          "n_procs", "geometry_name", "geometry_class")},
        "geometry": _geometry_spec(meta, p),
        "result": st["result"],
        "warnings": st.get("warnings", []),
        "y_plus": _report_field(case, "y_plus"),
        "mesh": _report_field(case, "mesh"),
    }
    (out / "run.json").write_text(json.dumps(run, indent=1))
    (out / "history.json").write_text(json.dumps(history, separators=(",", ":")))
    return sum(f.stat().st_size for f in out.iterdir())


def _report_field(case: Path, key: str):
    f = case / "windlab_report.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text()).get(key)


def _geometry_spec(meta: dict, params: dict) -> dict:
    """What is needed to regenerate the shape: the geometry.json of the run's shape is not kept with
    the run, so the lesson's run step carries the spec; this is only a cross-check (name)."""
    return {"name": params.get("geometry_name"), "class": params.get("geometry_class"),
            "body_min": meta.get("body_min"), "body_max": meta.get("body_max")}


def load_pack(lesson_id: str, name: str) -> dict:
    d = pack_dir(lesson_id, name)
    f = d / "run.json"
    if not f.is_file():
        raise KeyError(name)
    run = json.loads(f.read_text())
    if run.get("pack_version") != PACK_VERSION:
        raise LessonError("This pre-computed result was made for another version of WindLab.")
    run["files"] = sorted(x.name for x in d.iterdir() if x.is_file())
    run["size_bytes"] = sum(x.stat().st_size for x in d.iterdir() if x.is_file())
    return run


def install_pack_files(lesson_id: str, name: str, case_dir: Path) -> None:
    """Put a pack's pictures and histories where a finished run keeps them (case_dir already built)."""
    d = pack_dir(lesson_id, name)
    case = Path(case_dir)
    run = load_pack(lesson_id, name)
    sweep = run["params"].get("kind") == "sweep"
    for f in d.iterdir():
        m = re.fullmatch(r"(surface|slice)(_\d+)?\.bin\.gz", f.name)
        if m:
            (case / f"windlab_{m.group(1)}{m.group(2) or ''}.bin").write_bytes(_gz_read(f))
            continue
        m = re.fullmatch(r"sliceinfo(_\d+)?\.json\.gz", f.name)
        if m:
            text = _gz_read(f).decode()
            json.loads(text)  # must be JSON
            (case / f"windlab_sliceinfo{m.group(1) or ''}.json").write_text(text)
    history = json.loads((d / "history.json").read_text())
    for key, rows in history.items():
        i = int(key)
        target = (case / "sweep" / f"p{i}" if sweep else case) / "postProcessing" / "forceCoeffs1" / "0"
        target.mkdir(parents=True, exist_ok=True)
        with (target / "coefficient.dat").open("w") as fh:
            fh.write("# Time Cd Cl\n")
            for it, cl, cd in rows:
                fh.write(f"{int(it)} {float(cd):.6g} {float(cl):.6g}\n")
    # a report with the mesh / y+ so the page's details read as for a real run
    rep = {"run_id": case.name, "case_dir": str(case), "n_procs": run["params"].get("n_procs"),
           "mesh": run.get("mesh") or {}, "y_plus": run.get("y_plus") or {}, "coefficients": {},
           "converged": run["result"].get("converged"), "iterations": run["result"].get("iterations"),
           "source": "lesson pack"}
    (case / "windlab_report.json").write_text(json.dumps(rep, indent=1))
    # sanity: the recorded result must match the stored force history (last window mean)
    c = results.read_force_coeffs(case if not sweep else case / "sweep" / "p0")
    if c is None or len(c["Time"]) == 0:
        raise LessonError("The pre-computed result has no force history.")


# --------------------------------------------------------------------------- answers (local)


def add_answer(data_dir: Path, lesson_id: str, step_id: str, answer, correct: bool | None = None,
               now: float | None = None) -> dict:
    lid = parse_id(lesson_id)
    if not _ID.match(str(step_id) or "") or len(str(step_id)) > 40:
        raise LessonError("That lesson step was not found.")
    lesson = _all().get(lid)
    if lesson is None or step_id not in {s["id"] for s in lesson["steps"]}:
        raise LessonError("That lesson step was not found.")
    if isinstance(answer, bool) or not isinstance(answer, (str, int, float)):
        raise LessonError("An answer is a short text or a choice.")
    text = clean_text(str(answer), MAX_ANSWER) if isinstance(answer, str) else answer
    rec = {"time": round(time.time() if now is None else now, 1), "lesson": lid, "step": step_id, "answer": text,
           "correct": correct, "version": __version__}
    path = Path(data_dir) / ANSWERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_answers(data_dir: Path, lesson_id: str | None = None) -> list[dict]:
    path = Path(data_dir) / ANSWERS_FILE
    out = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and (lesson_id is None or rec.get("lesson") == lesson_id):
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


def latest_answers(data_dir: Path, lesson_id: str) -> dict[str, dict]:
    """The most recent answer per step (what the page restores on reload)."""
    out: dict[str, dict] = {}
    for rec in read_answers(data_dir, parse_id(lesson_id)):
        out[rec["step"]] = rec
    return out


__all__ = ["LESSON_ROOT", "LessonError", "add_answer", "check_lesson", "get_lesson", "install_pack_files",
           "latest_answers", "list_lessons", "load_pack", "pack_dir", "pack_from_run", "read_answers"]
