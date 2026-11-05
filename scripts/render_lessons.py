#!/usr/bin/env python3
"""Render the printable worksheets and instructor guides from the lessons (one source: lesson.json + packs).

    python scripts/render_lessons.py          # writes docs/lessons/<id>-worksheet.md and <id>-instructor.md

tests/test_lessons.py checks the rendered files are up to date.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from windlab import lessons  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs" / "lessons"


def _pack_table(lesson_id: str, step: dict) -> list[str]:
    """The pre-computed numbers of a run step (what students should see), as a Markdown table."""
    try:
        run = lessons.load_pack(lesson_id, step["pack"])
    except (KeyError, lessons.LessonError):
        return ["*(no pre-computed result installed for this step)*", ""]
    r, p = run["result"], run["params"]
    out = [f"Pre-computed on {run.get('computed')} (Quick mesh, {r.get('cells', '?'):,} cells, "
           f"{r.get('solver_seconds', 0):.0f} s of solver time on {p.get('n_procs')} cores). Reynolds number "
           f"{p.get('reynolds', 0):,.0f}; reference area {p.get('reference_area', 0):.4g} m²."]
    if p.get("kind") == "sweep":
        out += ["", "| angle (°) | CL | CD | L/D | iterations | settled |", "|---:|---:|---:|---:|---:|:--|"]
        for pt in r.get("points", []):
            if pt.get("status") == "done":
                out.append(f"| {pt['alpha_deg']:g} | {pt['cl']:.3f} | {pt['cd']:.4f} | {pt['l_over_d']:.1f} | "
                           f"{pt['iterations']} | {'yes' if pt.get('converged') else 'no'} |")
        if r.get("max_l_over_d_alpha") is not None:
            out.append(f"\nBest L/D {r['max_l_over_d']:.1f} at {r['max_l_over_d_alpha']:g}°."
                       + (f" {r['stall_note']}" if r.get("stall_note") else ""))
    else:
        out += ["", f"CL = {r['cl']:.3f}, CD = {r['cd']:.4f}, L/D = {r['l_over_d']:.1f}; {r['iterations']} iterations "
                f"({'settled' if r.get('converged') else 'did not settle'})."]
    if run.get("warnings"):
        out.append("\nNotes shown with the result: " + " ".join(run["warnings"]))
    return out + [""]


def worksheet(lesson: dict) -> str:
    L = [f"# Worksheet: {lesson['title']}", "",
         f"*WindLab lesson `{lesson['id']}` · about {lesson['minutes']} minutes · uncalibrated prototype, not yet "
         "validated.*", "", "Name: ____________________    Date: ____________", "",
         f"{lesson['summary']}", "", "## Learning objectives", ""]
    L += [f"{i + 1}. {o}" for i, o in enumerate(lesson["objectives"])]
    L += ["", "## Steps", "", "Work through the steps in order. **Write each prediction before you run.**", ""]
    q = 0
    for i, s in enumerate(lesson["steps"], 1):
        kind = s["kind"]
        if kind == "intro":
            L += [f"### {i}. {s['title']}", "", s["text"], ""]
        elif kind in ("predict", "observe", "explain"):
            L += [f"### {i}. {s['title']}", "", s["prompt"], "", "> " + ("\n> " * 4), ""]
        elif kind == "run":
            c = s["conditions"]
            L += [f"### {i}. {s['title']}", "", f"- Shape: {_shape_text(s['shape'])}",
                  f"- Airspeed: {c['airspeed']} m/s",
                  (f"- Angles of attack: {', '.join(str(a) for a in c['angles'])}° (sweep)" if c.get("kind") == "sweep"
                   else f"- Angle of attack: {c['alpha_deg']}°"), "- Mesh: Quick", "", s.get("text", ""), "",
                  "Write down the numbers you read:", "", "> " + ("\n> " * 3), ""]
        elif kind == "question":
            q += 1
            L += [f"### {i}. Question {q}", "", s["prompt"], ""]
            L += [f"- [ ] {c}" for c in s["choices"]]
            L += [""]
        elif kind == "homework":
            L += [f"### {i}. {s['title']}", "", s["text"], ""]
    L += ["---", "", "Rendered from `windlab/lessons/" + lesson["id"] + "/lesson.json` by `scripts/render_lessons.py`; "
          "the same lesson runs in the app under *Lessons*.", ""]
    return "\n".join(L)


def _shape_text(shape: dict) -> str:
    if "naca" in shape:
        n = shape["naca"]
        return f"NACA {n['code']} wing, chord {n['chord']} m, span {n['span']} m"
    b = dict(shape["builtin"])
    kind = b.pop("shape")
    return f"built-in {kind}: " + ", ".join(f"{k} {v}" for k, v in b.items())


def instructor(lesson: dict) -> str:
    L = [f"# Instructor guide: {lesson['title']}", "",
         f"*WindLab lesson `{lesson['id']}` · {lesson['minutes']} minutes · "
         "uncalibrated prototype, not yet validated. Start the class with `windlab start --lesson "
         f"{lesson['id']}` (teacher mode: the page opens in the lesson and hides the upload and reference-value "
         "controls).*", "", lesson["summary"], "", "## Learning objectives", ""]
    L += [f"{i + 1}. {o}" for i, o in enumerate(lesson["objectives"])]
    L += ["", "## Minute by minute", "", "| minutes | what happens |", "|---:|:--|"]
    t = 0
    for item in lesson.get("timeline", []):
        L.append(f"| {t}-{t + item['minutes']} | {item['what']} |")
        t += item["minutes"]
    L += ["", f"Total {t} minutes. Quick runs take 1-5 minutes each on a student laptop; a sweep about that per angle. "
          "If the laptops are slow, use the pre-computed results for every run except one, so that students see one "
          "live run settle.", "", "## Expected results and answers", ""]
    q = 0
    for s in lesson["steps"]:
        if s["kind"] == "predict":
            L += [f"**{s['title']}** — prompt: {s['prompt']}", "",
                  "Accept any prediction; the point is to commit to one before the run. Ask two or three students to "
                  "read theirs out.", ""]
        elif s["kind"] == "run":
            L += [f"**{s['title']}**", ""] + _pack_table(lesson["id"], s)
        elif s["kind"] == "observe":
            L += [f"**{s['title']}** — prompt: {s['prompt']}", "",
                  "Expected: the numbers in the result table(s) above.", ""]
        elif s["kind"] == "explain":
            L += [f"**{s['title']}** — prompt: {s['prompt']}", "", f"Model answer: {s.get('text', '')}", ""]
        elif s["kind"] == "question":
            q += 1
            L += [f"**Question {q}.** {s['prompt']}", ""]
            L += [f"- {'**' if k == s['answer'] else ''}{c}{' (correct)**' if k == s['answer'] else ''}"
                  for k, c in enumerate(s["choices"])]
            L += ["", f"  {s.get('explanation', '')}", ""]
        elif s["kind"] == "homework":
            L += [f"**{s['title']}** — {s['text']}", ""]
    L += ["## Common misconceptions", ""]
    L += [f"- {m}" for m in lesson.get("misconceptions", [])]
    L += ["", "## Where the numbers come from", "",
          "The pre-computed results were made with WindLab's own Quick preset in the dev container on 2026-11-03 "
          "(4 cores); the method's accuracy is documented in `docs/TEACHING_NOTES.md`, `validation/naca0012_tmr.md`, "
          "`validation/wing3d_mesh_study.md` and `validation/bluff_bodies.md`. Every coefficient in the app carries an "
          "expected-error range with a \"Why this range?\" note; the lesson *Why simulations disagree* teaches how to "
          "read it. Student answers are stored locally and exported with `windlab feedback export`.", "",
          "Rendered from `windlab/lessons/" + lesson["id"] + "/lesson.json` by `scripts/render_lessons.py`.", ""]
    return "\n".join(L)


def render_all() -> dict[str, str]:
    out = {}
    for item in lessons.list_lessons():
        lesson = lessons.get_lesson(item["id"])
        out[f"{item['id']}-worksheet.md"] = worksheet(lesson)
        out[f"{item['id']}-instructor.md"] = instructor(lesson)
    return out


if __name__ == "__main__":
    DOCS.mkdir(parents=True, exist_ok=True)
    for name, text in render_all().items():
        (DOCS / name).write_text(text)
        print("wrote", DOCS / name)
    index = ["# WindLab lessons", "", "Printable worksheets and instructor guides, rendered from the lessons that ship "
             "in the app (`windlab/lessons/<id>/`). Open the app's *Lessons* page to run them.", ""]
    for item in lessons.list_lessons():
        index.append(f"- **{item['title']}** ({item['minutes']} min): [worksheet]({item['id']}-worksheet.md) · "
                     f"[instructor guide]({item['id']}-instructor.md)")
    (DOCS / "README.md").write_text("\n".join(index) + "\n")
    json.dumps(render_all())  # all text, no surprises
