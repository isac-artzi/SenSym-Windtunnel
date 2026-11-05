#!/usr/bin/env python3
"""Run the lesson cases through the real app and export compact result packs.

    python scripts/build_lesson_packs.py run    [--data /runs/lesson-packs] [--np 4] [--only a,b]
    python scripts/build_lesson_packs.py export [--data /runs/lesson-packs] [--out windlab/lessons]

``run`` starts a WindLab app (no HTTP server) on the given data folder, submits every run the
lessons need (one solver run at a time, as the app always does) and records the run ids in
``lesson_runs.json`` there; it is safe to call again - finished runs are reused.
``export`` turns the finished runs into ``windlab/lessons/<id>/pack/`` (see
windlab.lessons.pack_from_run for the pack format).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.asgi_client import make_client  # noqa: E402
from windlab.app import Settings, create_app  # noqa: E402

# Every run a lesson needs. key: (lesson id, run name); shape: NACA wing or built-in bluff body.
RUNS: list[dict] = [
    # (a) lift vs angle of attack: the default wing as a sweep
    {"lesson": "lift-vs-angle", "name": "naca0012-sweep", "naca": ("0012", 0.3, 0.6), "airspeed": 20.0,
     "angles": [-4, 0, 4, 8, 12, 16]},
    # (b) Reynolds number and scale: same wing at three speeds, then two sizes at 20 m/s
    {"lesson": "reynolds-and-scale", "name": "v10", "naca": ("0012", 0.3, 0.6), "airspeed": 10.0, "alpha_deg": 8.0},
    {"lesson": "reynolds-and-scale", "name": "v20", "naca": ("0012", 0.3, 0.6), "airspeed": 20.0, "alpha_deg": 8.0},
    {"lesson": "reynolds-and-scale", "name": "v40", "naca": ("0012", 0.3, 0.6), "airspeed": 40.0, "alpha_deg": 8.0},
    {"lesson": "reynolds-and-scale", "name": "half-size", "naca": ("0012", 0.15, 0.3), "airspeed": 20.0,
     "alpha_deg": 8.0},
    {"lesson": "reynolds-and-scale", "name": "double-size", "naca": ("0012", 0.6, 1.2), "airspeed": 20.0,
     "alpha_deg": 8.0},
    # (c) streamlining a bluff body: the built-in shapes at 20 m/s, 0 deg (reference length ~0.1-0.3 m)
    {"lesson": "streamlining", "name": "sphere", "builtin": ("sphere", {"diameter": 0.2}), "airspeed": 20.0,
     "alpha_deg": 0.0},
    {"lesson": "streamlining", "name": "cylinder", "builtin": ("cylinder", {"diameter": 0.15, "length": 0.45}),
     "airspeed": 20.0, "alpha_deg": 0.0},
    {"lesson": "streamlining", "name": "flat-plate", "builtin": ("plate", {"height": 0.2, "width": 0.2}),
     "airspeed": 20.0, "alpha_deg": 0.0},
    {"lesson": "streamlining", "name": "cube", "builtin": ("cube", {"side": 0.2}), "airspeed": 20.0, "alpha_deg": 0.0},
    {"lesson": "streamlining", "name": "teardrop", "builtin": ("teardrop", {"diameter": 0.2, "fineness": 3.0}),
     "airspeed": 20.0, "alpha_deg": 0.0},
    # (d) aspect ratio: spans 0.3 and 1.2 m (AR 1 and 4); AR 2 comes from the lesson (a) sweep
    {"lesson": "aspect-ratio", "name": "ar1-sweep", "naca": ("0012", 0.3, 0.3), "airspeed": 20.0, "angles": [0, 4, 8]},
    {"lesson": "aspect-ratio", "name": "ar4-sweep", "naca": ("0012", 0.3, 1.2), "airspeed": 20.0, "angles": [0, 4, 8]},
    # (e) why simulations disagree: one Quick run at 10 deg to set beside the mesh study's Standard / fine numbers
    {"lesson": "why-simulations-disagree", "name": "quick10", "naca": ("0012", 0.3, 0.6), "airspeed": 20.0,
     "alpha_deg": 10.0},
]


def manifest_path(data: Path) -> Path:
    return data / "lesson_runs.json"


def load_manifest(data: Path) -> dict:
    p = manifest_path(data)
    return json.loads(p.read_text()) if p.is_file() else {}


def save_manifest(data: Path, m: dict) -> None:
    manifest_path(data).write_text(json.dumps(m, indent=2))


def make_geometry(client, spec: dict) -> str:
    if "naca" in spec:
        code, chord, span = spec["naca"]
        r = client.post("/api/geometry/naca", json={"code": code, "chord": chord, "span": span})
    else:
        kind, params = spec["builtin"]
        r = client.post("/api/geometry/builtin", json={"shape": kind, **params})
    if r.status_code != 200:
        raise SystemExit(f"geometry for {spec['lesson']}/{spec['name']} failed: {r.status_code} {r.text}")
    return r.json()["geometry_id"]


def submit(client, spec: dict, gid: str) -> str:
    body = {"geometry_id": gid, "airspeed": spec["airspeed"], "preset": "quick"}
    if "angles" in spec:
        body.update(kind="sweep", angles=spec["angles"])
    else:
        body["alpha_deg"] = spec["alpha_deg"]
    r = client.post("/api/runs", json=body)
    if r.status_code != 202:
        raise SystemExit(f"run {spec['lesson']}/{spec['name']} refused: {r.status_code} {r.text}")
    return r.json()["run_id"]


def cmd_run(args) -> int:
    data = Path(args.data)
    data.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    app = create_app(Settings(data_dir=data, n_procs=args.n_procs, keep_runs=500, disk_cap_bytes=200 * 2**30))
    client = make_client(app)
    manifest = load_manifest(data)
    runs = app.fastapi.state.runs
    todo = []
    for spec in RUNS:
        key = f"{spec['lesson']}/{spec['name']}"
        if only and spec["lesson"] not in only and spec["name"] not in only:
            continue
        rid = manifest.get(key, {}).get("run_id")
        if rid:
            try:
                st = runs.status(rid)
                if st["status"] == "done":
                    print(f"{key}: already done ({rid[:8]})")
                    continue
            except KeyError:
                pass
        gid = make_geometry(client, spec)
        rid = submit(client, spec, gid)
        manifest[key] = {"run_id": rid, "geometry_id": gid, "spec": {k: v for k, v in spec.items()}}
        save_manifest(data, manifest)
        print(f"{key}: submitted {rid[:8]}", flush=True)
        todo.append((key, rid))
    t0 = time.monotonic()
    last = ""
    while todo:
        time.sleep(2)
        key, rid = todo[0]
        st = runs.status(rid)
        line = f"{key}: {st['status']} {st['stage_label']} it {st.get('iteration')}"
        if line != last:
            print(f"[{time.monotonic() - t0:6.0f} s] {line}", flush=True)
            last = line
        if st["status"] not in ("queued", "running"):
            r = st.get("result") or {}
            print(f"  -> {st['status']}: CL {r.get('cl')} CD {r.get('cd')} iterations {r.get('iterations')} "
                  f"solver {r.get('solver_seconds')} s" + (f" ERROR {st.get('error')}" if st.get("error") else ""),
                  flush=True)
            manifest[key]["status"] = st["status"]
            save_manifest(data, manifest)
            todo.pop(0)
    runs.stop()
    print("all lesson runs finished")
    return 0


def cmd_export(args) -> int:
    from windlab import lessons

    data = Path(args.data)
    out = Path(args.out)
    app = create_app(Settings(data_dir=data, n_procs=1, keep_runs=500, disk_cap_bytes=200 * 2**30))
    manifest = load_manifest(data)
    total = 0
    for key, entry in sorted(manifest.items()):
        lesson_id, name = key.split("/", 1)
        st = app.fastapi.state.runs.get(entry["run_id"])
        if st.status != "done":
            print(f"{key}: not finished ({st.status}), skipped")
            continue
        size = lessons.pack_from_run(st.case_dir, out / lesson_id / "pack" / name, name=name)
        total += size
        print(f"{key}: {size / 1024:.0f} KB")
    app.fastapi.state.runs.stop()
    print(f"total {total / 2**20:.1f} MB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", default="/runs/lesson-packs")
    r.add_argument("--np", type=int, default=4, dest="n_procs")
    r.add_argument("--only", default=None, help="comma-separated lesson ids or run names")
    e = sub.add_parser("export")
    e.add_argument("--data", default="/runs/lesson-packs")
    e.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "windlab" / "lessons"))
    args = ap.parse_args()
    return cmd_run(args) if args.cmd == "run" else cmd_export(args)


if __name__ == "__main__":
    sys.exit(main())
