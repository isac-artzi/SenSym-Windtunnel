"""Command line: ``windlab start`` runs the web app and prints the address to open.

    windlab start                      # http://127.0.0.1:8080, the usual install
    windlab start --host 0.0.0.0       # inside a Docker container only (the container
                                       # publishes the port on the host's 127.0.0.1)
    windlab start --port 8081 --procs 6 --data ~/windlab-data
    windlab doctor                     # is this computer ready? (about 30 s)
    windlab update                     # git pull + pip install . in WindLab's own venv
    windlab benchmark [--np N]         # time the standard Quick run, save JSON + text
    windlab feedback export [--out F]  # one CSV of trial feedback + anonymous usage to e-mail
    windlab lessons                    # list the guided lessons
    windlab start --lesson lift-vs-angle   # teacher mode: open straight into a lesson

``windlab start`` and ``windlab benchmark`` show the educational-use notice (DISCLAIMER.md) on
first use and wait for ``yes``; ``--accept-disclaimer`` or ``WINDLAB_ACCEPT_DISCLAIMER=1``
accept it for scripts and CI (windlab.disclaimer).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

from windlab import __version__, disclaimer

DEFAULT_PORT = 8080
DISCLAIMER_NOT_ACCEPTED = 3  # exit code when the notice was not accepted
DEFAULT_KEEP_RUNS = 20
DEFAULT_DISK_CAP_GB = 10.0


def default_data_dir() -> Path:
    """Where geometries and runs go: $WINDLAB_DATA, else /runs/app in the dev container
    (where /runs is a Linux volume), else ~/.local/share/windlab."""
    env = os.environ.get("WINDLAB_DATA")
    if env:
        return Path(env).expanduser()
    runs = Path("/runs")
    if runs.is_dir() and os.access(runs, os.W_OK):
        return runs / "app"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "windlab"


def default_procs() -> int:
    """Solver ranks per run unless --procs says otherwise: cores minus one, at most 8
    (:func:`windlab.machine.solver_cores`)."""
    from windlab import machine

    return machine.solver_cores()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="windlab", description="SenSym WindLab - a virtual wind tunnel.")
    ap.add_argument("--version", action="version", version=f"WindLab {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    st = sub.add_parser("start", help="start WindLab and print the address to open in your browser")
    st.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default 127.0.0.1 = this computer only). "
                         "Use 0.0.0.0 only inside a Docker container.")
    _accept_flag(st)
    st.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    st.add_argument("--data", type=Path, default=None, help="folder for shapes and runs")
    st.add_argument("--procs", type=int, default=None,
                    help="processor cores per run (default: this computer's cores minus one, at most 8)")
    st.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="extra host name the browser may use (besides localhost / 127.0.0.1)")
    st.add_argument("--keep-runs", type=int, default=DEFAULT_KEEP_RUNS, metavar="N",
                    help=f"keep at most N finished runs; older ones are deleted (default {DEFAULT_KEEP_RUNS})")
    st.add_argument("--disk-cap-gb", type=float, default=DEFAULT_DISK_CAP_GB, metavar="GB",
                    help=f"largest size of the data folder; oldest finished runs are deleted first "
                         f"(default {DEFAULT_DISK_CAP_GB:g})")
    st.add_argument("--lesson", default=None, metavar="ID",
                    help="teacher mode: open the page straight into this lesson and hide the advanced controls "
                         "(ids: see  windlab lessons)")

    sub.add_parser("lessons", help="list the guided lessons and their ids (for  windlab start --lesson ID)")

    up = sub.add_parser("update", help="get the latest WindLab (git pull) and install it into this environment")
    up.add_argument("--repo", type=Path, default=None, help="the WindLab checkout (default: where this copy came from)")

    dr = sub.add_parser("doctor", help="check that this computer is ready for WindLab (about 30 s)")
    dr.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to check (default {DEFAULT_PORT})")
    dr.add_argument("--data", type=Path, default=None, help="folder for shapes and runs")
    dr.add_argument("--procs", type=int, default=None, help="cores for the test run (default 2)")
    dr.add_argument("--no-test-run", action="store_true", help="skip the tiny test simulation")

    bm = sub.add_parser("benchmark", help="time the standard Quick run (NACA 0012 wing at 10 deg) on this computer")
    bm.add_argument("--np", type=int, default=None, dest="n_procs",
                    help="cores to use (default: what windlab start uses - cores minus one, at most 8)")
    bm.add_argument("--alpha", type=float, default=10.0, help="angle of attack in degrees (default 10)")
    bm.add_argument("--out", type=Path, default=None,
                    help="JSON file to write (default ./windlab-benchmark-<host>-<date>-np<N>.json; "
                         "a .txt summary is written next to it)")
    bm.add_argument("--data", type=Path, default=None, help="folder for the temporary case")
    bm.add_argument("--keep", action="store_true", help="keep the case folder afterwards")
    _accept_flag(bm)

    fb = sub.add_parser("feedback", help="trial feedback: export it as one CSV file to send to your instructor")
    fb_sub = fb.add_subparsers(dest="feedback_command", required=True)
    ex = fb_sub.add_parser("export", help="write all feedback and an anonymous usage summary to a CSV file")
    ex.add_argument("--out", type=Path, default=None,
                    help="file to write (default ./windlab-feedback-<date>.csv); '-' prints it instead")
    ex.add_argument("--data", type=Path, default=None, help="WindLab's data folder (default: the usual one)")
    return ap


def _accept_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--accept-disclaimer", action="store_true",
                        help="accept the educational-use notice (DISCLAIMER.md) without being asked; "
                             f"scripts can set {disclaimer.ENV_ACCEPT}=1 instead")


def _disclaimer_ok(args) -> bool:
    """Print the notice and ask for 'yes' unless it was accepted before (windlab.disclaimer)."""
    return disclaimer.require_cli_acceptance(getattr(args, "accept_disclaimer", False))


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def start(args, serve=None) -> int:
    from windlab.app import Settings, create_app
    from windlab.runner import FOAM_BASHRC

    if not 1 <= args.port <= 65535:
        print("windlab: --port must be between 1 and 65535", file=sys.stderr)
        return 2
    data = (args.data or default_data_dir()).expanduser().resolve()
    procs = max(1, min(int(args.procs or default_procs()), os.cpu_count() or 1, 64))
    try:
        data.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"windlab: cannot use the data folder {data}: {e}", file=sys.stderr)
        return 2
    if args.keep_runs < 1 or not 0.1 <= args.disk_cap_gb <= 10_000:
        print("windlab: --keep-runs must be at least 1 and --disk-cap-gb between 0.1 and 10000", file=sys.stderr)
        return 2
    if not _disclaimer_ok(args):
        return DISCLAIMER_NOT_ACCEPTED
    try:
        app = create_app(Settings(data_dir=data, n_procs=procs, allowed_hosts=args.allow_host,
                                  keep_runs=args.keep_runs, disk_cap_bytes=int(args.disk_cap_gb * 2**30),
                                  lesson=getattr(args, "lesson", None)))
    except ValueError as e:  # an unknown --lesson id
        print(f"windlab: {e}", file=sys.stderr)
        return 2

    browse_host = "localhost" if args.host in ("0.0.0.0", "::", "127.0.0.1", "localhost") else args.host
    print(f"WindLab {__version__} (uncalibrated prototype - not yet validated)")
    print(f"  {disclaimer.FOOTER_ASCII}")
    print(f"  shapes and runs: {data}")
    print(f"  cores per run:   {procs}" + ("" if args.procs else f" (of {os.cpu_count() or 1}; --procs N to change)"))
    print(f"  keeps:           the last {args.keep_runs} finished runs, at most {args.disk_cap_gb:g} GB")
    if getattr(args, "lesson", None):
        print(f"  teacher mode:    lesson '{args.lesson}' opens first; advanced controls are hidden")
    if not Path(FOAM_BASHRC).is_file():
        print(f"  WARNING: OpenFOAM was not found at {FOAM_BASHRC}; runs will fail. See docs/INSTALL.md.")
    if not _is_loopback(args.host):
        print(f"  NOTE: listening on {args.host}, not just this computer. Only do this inside a Docker container,")
        print("        whose port is published on the host's 127.0.0.1 only.")
    print(f"\n  Open this address in your browser:  http://{browse_host}:{args.port}/")
    if args.host == "0.0.0.0":
        print("  (Docker: open the port the container publishes, usually http://localhost:8080/)")
    print("  Press Ctrl+C here to stop WindLab.\n", flush=True)

    if serve is None:
        import uvicorn

        def serve(app, host, port):
            uvicorn.run(app, host=host, port=port, log_level="warning", server_header=False,
                        proxy_headers=False, timeout_keep_alive=5, limit_concurrency=64)

    serve(app, args.host, args.port)
    return 0


def _data_dir(args) -> Path | None:
    data = (args.data or default_data_dir()).expanduser().resolve()
    try:
        data.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"windlab: cannot use the data folder {data}: {e}", file=sys.stderr)
        return None
    return data


def doctor(args) -> int:
    from windlab.doctor import default_test_procs, run_checks

    if not 1 <= args.port <= 65535:
        print("windlab: --port must be between 1 and 65535", file=sys.stderr)
        return 2
    data = _data_dir(args)
    if data is None:
        return 2
    procs = max(1, min(int(args.procs or default_test_procs()), os.cpu_count() or 1, 64))
    checks = run_checks(data, args.port, procs, test_run=not args.no_test_run)
    return 1 if any(c.status == "fail" for c in checks) else 0


def benchmark(args, runner=None) -> int:
    from windlab import benchmark as bm
    from windlab.runner import RunError

    data = _data_dir(args)
    if data is None:
        return 2
    n = args.n_procs or bm.default_np()
    if not 1 <= n <= 256:
        print("windlab: --np must be between 1 and 256", file=sys.stderr)
        return 2
    if not -20 <= args.alpha <= 20:
        print("windlab: --alpha must be between -20 and 20", file=sys.stderr)
        return 2
    if not _disclaimer_ok(args):
        return DISCLAIMER_NOT_ACCEPTED
    try:
        result = bm.run_benchmark(data, n, alpha_deg=args.alpha, keep=args.keep, runner=runner)
    except RunError as e:
        print(f"windlab benchmark: the run stopped in {e.stage}: {e}", file=sys.stderr)
        return 1
    print("\n" + bm.text_summary(result))
    js, txt = bm.save(result, args.out or bm.default_out_path(result))
    print(f"\nSaved {js} and {txt.name}")
    return 0


def feedback_export(args) -> int:
    from windlab import feedback

    data = (args.data or default_data_dir()).expanduser()
    if not data.is_dir():
        print(f"windlab: no WindLab data folder at {data} - has WindLab been started on this computer?",
              file=sys.stderr)
        return 1
    text = feedback.export_csv(data)
    if str(args.out) == "-":
        sys.stdout.write(text)
        return 0
    out = (args.out or Path(feedback.default_export_name())).expanduser()
    try:
        out.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"windlab: could not write {out}: {e.strerror or e}", file=sys.stderr)
        return 1
    n = len(feedback.read_feedback(data))
    print(f"Saved {out.resolve()}")
    print(f"  {n} feedback answer{'s' if n != 1 else ''} and an anonymous usage summary (no file or shape names).")
    print("  Please e-mail this file to your instructor. Thank you!")
    if out.resolve().as_posix().startswith("/home/") and Path("/mnt/c").is_dir():
        print("  Tip (Windows): type  explorer.exe .  in this Ubuntu window to open this folder in File Explorer,")
        print("  then drag the file to your Desktop (or open  \\\\wsl$\\  in File Explorer to find it).")
    return 0


def repo_dir() -> Path | None:
    """The git checkout this WindLab was installed from, if it still exists."""
    env = os.environ.get("WINDLAB_REPO")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parents[1]
    if (here / ".git").exists() and (here / "pyproject.toml").is_file():
        return here  # pip install -e, or running from the checkout
    home = Path.home() / "windlab"
    if (home / ".git").exists() and (home / "pyproject.toml").is_file():
        return home  # the usual install (docs/INSTALL.md: git clone into ~/windlab)
    return None


def update(args, run=None) -> int:
    """``windlab update``: git pull in the checkout, then pip install . into this Python."""
    import subprocess

    run = run or (lambda cmd, **kw: subprocess.run(cmd, check=False, **kw))
    repo = (args.repo or repo_dir())
    if repo is None or not (repo / ".git").exists():
        print("windlab update: could not find the WindLab checkout (a folder with .git and pyproject.toml).\n"
              "  If you installed by hand, run in the folder you cloned:  git pull  then  pip install .\n"
              "  Or tell me where it is:  windlab update --repo ~/windlab", file=sys.stderr)
        return 2
    repo = repo.expanduser().resolve()
    print(f"WindLab {__version__}: updating the copy in {repo}")
    print("  1/2  git pull ...", flush=True)
    r = run(["git", "-C", str(repo), "pull", "--ff-only"])
    if r.returncode != 0:
        print("windlab update: git pull did not succeed. Check the internet connection and try again.\n"
              "  If you changed files in the WindLab folder, git refuses to overwrite them: run\n"
              "  git -C ~/windlab status  to see which (git stash  puts your changes aside).\n"
              "  If you downloaded WindLab as a ZIP instead of git clone, download the new ZIP and run\n"
              "  pip install .  in the unpacked folder (with the WindLab environment active).", file=sys.stderr)
        return 1
    print(f"  2/2  pip install . (into {sys.prefix}) ...", flush=True)
    r = run([sys.executable, "-m", "pip", "install", "--quiet", str(repo)])
    if r.returncode != 0:
        print("windlab update: the install step failed. Make sure the WindLab environment is active\n"
              "  (source ~/windlab-venv/bin/activate) and try again; if it keeps failing, copy the message above.",
              file=sys.stderr)
        return 1
    r = run([sys.executable, "-c", "import windlab, sys; sys.stdout.write(windlab.__version__)"],
            capture_output=True, text=True)
    new = (getattr(r, "stdout", "") or "").strip() or "?"
    print(f"Done: WindLab {new} is installed. Start it with  windlab start  (and run  windlab doctor  if anything "
          "looks wrong).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        return start(args)
    if args.command == "doctor":
        return doctor(args)
    if args.command == "benchmark":
        return benchmark(args)
    if args.command == "feedback" and args.feedback_command == "export":
        return feedback_export(args)
    if args.command == "lessons":
        return list_lessons()
    if args.command == "update":
        return update(args)
    return 2


def list_lessons() -> int:
    from windlab import lessons

    for item in lessons.list_lessons():
        pack = "pre-computed results included" if item["pack_available"] else "no pre-computed results"
        print(f"{item['id']:28s} {item['title']} ({item['minutes']} min; {pack})")
    print("\nStart straight into one with:  windlab start --lesson <id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
