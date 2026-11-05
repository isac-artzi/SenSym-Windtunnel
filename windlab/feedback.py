"""Trial feedback and an anonymous usage log, both kept on the student's own computer.

* ``feedback.jsonl`` in the data folder: one JSON object per answer to the "How was this?"
  panel (two 1-5 ratings, a comment, an optional name or initials).
* ``usage.jsonl``: one JSON object per event - a run or sweep started, an upload, a
  cancel, a run that finished (with its run time) or stopped with a problem (with the
  plain-language message the student saw). No file names, shape names, paths or numbers
  the student typed are recorded.

``windlab feedback export`` turns both into one CSV a student can e-mail: the usage
summary and the computer (OS, cores, RAM) as comment lines at the top, then one row per
feedback answer. Nothing is ever sent anywhere by WindLab itself.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import statistics
import threading
import time
from collections import Counter
from pathlib import Path

from windlab import __version__

log = logging.getLogger("windlab.feedback")

FEEDBACK_FILE = "feedback.jsonl"
USAGE_FILE = "usage.jsonl"
MAX_COMMENT = 2000
MAX_NAME = 80
EVENTS = ("run", "sweep", "upload", "upload_failed", "naca", "cancel", "done", "error", "pack")

FEEDBACK_COLUMNS = ["time", "the results made sense to me (1-5)", "the app was easy to use (1-5)",
                    "what confused you or what would you change", "name or initials", "run", "WindLab version",
                    "kind", "lesson", "lesson step", "lesson answer", "answer correct"]

_lock = threading.Lock()
# a file path such as /home/jo/x.stl or C:\Users\jo (two or more parts, not "m/s" or "L/D")
_PATHISH = re.compile(r"(?<![\w/\\])(?:[A-Za-z]:)?(?:[\\/][\w.~$-]*\w){2,}")


def _append(path: Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _read(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # a half-written line after a crash: skip it
                if isinstance(rec, dict):
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


def clean_text(text: str | None, limit: int) -> str:
    """Printable text on one line, at most ``limit`` characters."""
    if not text:
        return ""
    s = "".join(ch if ch.isprintable() else " " for ch in str(text))
    return " ".join(s.split())[:limit]


# ----------------------------------------------------------------------------- feedback


def add_feedback(data_dir: Path, results_sensible: int | None, easy_to_use: int | None, comment: str | None,
                 name: str | None, run_id: str | None = None, now: float | None = None) -> dict:
    """Validate and store one answer; returns what was stored. ValueError with a plain message."""
    for label, v in (("results made sense", results_sensible), ("easy to use", easy_to_use)):
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 5):
            raise ValueError(f"The rating for \"{label}\" must be a whole number from 1 to 5.")
    comment, name = clean_text(comment, MAX_COMMENT), clean_text(name, MAX_NAME)
    if results_sensible is None and easy_to_use is None and not comment:
        raise ValueError("Please choose a rating or write a comment before sending.")
    rec = {"time": round(time.time() if now is None else now, 1), "results_sensible": results_sensible,
           "easy_to_use": easy_to_use, "comment": comment, "name": name, "run_id": run_id or None,
           "version": __version__}
    _append(Path(data_dir) / FEEDBACK_FILE, rec)
    return rec


def read_feedback(data_dir: Path) -> list[dict]:
    return _read(Path(data_dir) / FEEDBACK_FILE)


# ----------------------------------------------------------------------------- usage


class UsageLog:
    """Append-only event log; never raises (a full disk must not break a run)."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / USAGE_FILE

    def event(self, kind: str, message: str | None = None, seconds: float | None = None,
              stage: str | None = None) -> None:
        if kind not in EVENTS:
            return
        rec: dict = {"time": round(time.time(), 1), "event": kind}
        if message:
            rec["message"] = _PATHISH.sub("<file>", clean_text(message, 300))
        if seconds is not None:
            rec["seconds"] = round(float(seconds), 1)
        if stage:
            rec["stage"] = clean_text(stage, 40)
        try:
            _append(self.path, rec)
        except OSError as e:
            log.warning("usage log: %s", e)

    def read(self) -> list[dict]:
        return _read(self.path)


def usage_summary(events: list[dict]) -> dict:
    count = Counter(e.get("event") for e in events)
    times = [e["seconds"] for e in events if e.get("event") == "done" and isinstance(e.get("seconds"), (int, float))]
    errors = Counter(e.get("message") or "(no message)" for e in events if e.get("event") in ("error",
                                                                                               "upload_failed"))
    stamps = [e["time"] for e in events if isinstance(e.get("time"), (int, float))]
    return {
        "runs": count["run"],
        "sweeps": count["sweep"],
        "uploads": count["upload"] + count["upload_failed"],
        "uploads_rejected": count["upload_failed"],
        "built_in_wings": count["naca"],
        "finished": count["done"],
        "cancels": count["cancel"],
        "errors": count["error"],
        "pack_results_used": count["pack"],
        "median_run_seconds": round(statistics.median(times), 1) if times else None,
        "error_messages": errors.most_common(),
        "first_use": min(stamps) if stamps else None,
        "last_use": max(stamps) if stamps else None,
    }


# ----------------------------------------------------------------------------- export


def _comment(text: str) -> str:
    from windlab.app.runs import csv_comment

    return csv_comment(text)


def _day(t: float | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t)) if t else "-"


def export_csv(data_dir: Path, machine: dict | None = None) -> str:
    """One CSV: usage summary and computer as comment lines, then the feedback table."""
    if machine is None:
        from windlab import machine as m

        machine = m.summary()
    fb = read_feedback(data_dir)
    s = usage_summary(UsageLog(data_dir).read())
    buf = io.StringIO()
    c = _comment
    buf.write(c(f"WindLab {__version__} trial feedback - exported {time.strftime('%Y-%m-%d %H:%M')}"))
    buf.write(c("Anonymous usage summary (no file names or shape names; only the optional name column below "
                "is personal)"))
    buf.write(c(f"used from {_day(s['first_use'])} to {_day(s['last_use'])}"))
    buf.write(c(f"single runs started: {s['runs']}; sweeps started: {s['sweeps']}; finished: {s['finished']}; "
                f"cancelled: {s['cancels']}; stopped with a problem: {s['errors']}"))
    buf.write(c(f"STL uploads: {s['uploads']} (rejected: {s['uploads_rejected']}); built-in wings made: "
                f"{s['built_in_wings']}"))
    med = s["median_run_seconds"]
    buf.write(c("median run time: " + (f"{med:g} s ({med / 60:.1f} min)" if med is not None else "-")))
    for msg, n in s["error_messages"]:
        buf.write(c(f"problem shown {n}x: {msg}"))
    buf.write(c(f"computer: {machine.get('os', '-')}; WSL: {'yes' if machine.get('wsl') else 'no'}; "
                f"{machine.get('logical_cores', '-')} logical / {machine.get('physical_cores', '-')} physical cores; "
                f"{machine.get('ram_gb', '-')} GB RAM; CPU {machine.get('cpu', '-')}"))
    from windlab import lessons

    answers = lessons.read_answers(data_dir)
    graded = [a for a in answers if a.get("correct") is not None]
    buf.write(c(f"feedback answers: {len(fb)}; lesson answers: {len(answers)} "
                f"(comprehension questions right: {sum(1 for a in graded if a['correct'])} of {len(graded)}); "
                f"pre-computed lesson results used: {s['pack_results_used']}"))
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_NONNUMERIC)
    w.writerow(FEEDBACK_COLUMNS)
    for r in fb:
        w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("time") or 0)),
                    "" if r.get("results_sensible") is None else r["results_sensible"],
                    "" if r.get("easy_to_use") is None else r["easy_to_use"],
                    r.get("comment") or "", r.get("name") or "", (r.get("run_id") or "")[:8],
                    r.get("version") or "", "feedback", "", "", "", ""])
    for a in answers:
        w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(a.get("time") or 0)), "", "", "", "", "",
                    a.get("version") or "", "lesson answer", a.get("lesson") or "", a.get("step") or "",
                    _cell(a.get("answer")), "" if a.get("correct") is None else ("yes" if a["correct"] else "no")])
    return buf.getvalue()


def _cell(value) -> str:
    """A lesson answer as one CSV cell (no line breaks or '#', which pandas would treat as a comment)."""
    return clean_text(str(value), 2000).replace("#", "no. ") if value is not None else ""


def default_export_name() -> str:
    return f"windlab-feedback-{time.strftime('%Y-%m-%d')}.csv"
