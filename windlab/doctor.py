"""``windlab doctor``: is this computer ready to run WindLab?

Each check prints one plain-language line - PASS, WARN or FAIL - and, when something is
wrong, what to do about it. The last check builds a tiny wind-tunnel case (a small NACA
wing on a coarse mesh, 40 solver iterations) and runs it through the real OpenFOAM
pipeline: about 10-30 seconds.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from windlab import machine

GB = 2**30
EXPECTED_FOAM = "v2606"


@dataclass
class Check:
    name: str
    status: str  # pass | warn | fail
    message: str
    fix: str = ""

    def line(self) -> str:
        tag = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[self.status]
        out = f"[{tag}] {self.name}: {self.message}"
        if self.fix and self.status != "pass":
            out += f"\n       -> {self.fix}"
        return out


# --------------------------------------------------------------------------- checks


def check_openfoam() -> Check:
    from windlab import runner

    if not Path(runner.FOAM_BASHRC).is_file():
        return Check("OpenFOAM", "fail", f"not found ({runner.FOAM_BASHRC}).",
                     "Install it: see the OpenFOAM step of docs/INSTALL.md "
                     "(sudo apt install openfoam2606-default).")
    try:
        env = runner.foam_env()
    except Exception as e:  # noqa: BLE001 - any failure here means "not usable"
        return Check("OpenFOAM", "fail", f"found, but its settings could not be loaded ({e}).",
                     "Reinstall OpenFOAM (Step 2 of the install guide).")
    version = env.get("WM_PROJECT_VERSION", "?")
    missing = [exe for exe in ("blockMesh", "snappyHexMesh", "simpleFoam", "potentialFoam", "mpirun")
               if shutil.which(exe, path=env.get("PATH", "")) is None]
    if missing:
        return Check("OpenFOAM", "fail", f"version {version}, but these programs are missing: {', '.join(missing)}.",
                     "Reinstall with: sudo apt install --reinstall openfoam2606-default")
    if version != EXPECTED_FOAM:
        return Check("OpenFOAM", "warn", f"version {version} found; WindLab is tested with {EXPECTED_FOAM}.",
                     "It may work. If runs fail, install openfoam2606-default.")
    return Check("OpenFOAM", "pass", f"version {version}, all programs found.")


def check_cores(logical: int | None = None, physical: int | None = None, wsl: bool | None = None) -> Check:
    logical = machine.logical_cores() if logical is None else logical
    physical = machine.physical_cores() if physical is None else physical
    wsl = machine.is_wsl() if wsl is None else wsl
    ranks = machine.solver_cores(physical, logical, wsl)
    msg = (f"{physical} physical cores ({logical} logical); WindLab will use {ranks} core{'s' if ranks != 1 else ''} "
           f"per run (windlab start --procs N to change).")
    usable = max(logical, physical) if wsl else physical
    if usable >= 4:
        return Check("Processor", "pass", msg)
    if usable >= 2:
        return Check("Processor", "warn", msg + " Runs will be slow.",
                     "Close other programs during a run. If you have a .wslconfig file, make sure it does not "
                     "limit 'processors'.")
    return Check("Processor", "fail", msg + " WindLab needs at least 2 cores.",
                 "Check 'processors=' in C:\\Users\\<you>\\.wslconfig, or use a computer with more cores.")


def check_memory(total: int | None = None, available: int | None = None) -> Check:
    mem = machine.meminfo()
    total = mem["total"] if total is None else total
    available = mem["available"] if available is None else available
    msg = f"{total / GB:.1f} GB for Linux ({available / GB:.1f} GB free now)."
    if total >= 11 * GB:
        st, fix = "pass", ""
    elif total >= 7 * GB:
        st, fix = "warn", ("Quick runs need about 1-2 GB, but leave room: set memory=12GB in .wslconfig "
                           "(Step 1 of the install guide) if your laptop has 16 GB.")
    else:
        st, fix = "fail", "Give WSL more memory: memory=12GB in C:\\Users\\<you>\\.wslconfig, then wsl --shutdown."
    if st == "pass" and available < 2 * GB:
        st, fix = "warn", "Little memory is free right now: close large programs (SolidWorks, browsers) before a run."
    return Check("Memory", st, msg, fix)


def check_disk(data_dir: Path, free: int | None = None) -> Check:
    free = machine.free_disk(data_dir) if free is None else free
    msg = f"{free / GB:.0f} GB free where WindLab keeps its runs ({data_dir})."
    if free >= 10 * GB:
        return Check("Disk space", "pass", msg)
    if free >= 3 * GB:
        return Check("Disk space", "warn", msg, "WindLab can keep up to 10 GB of runs; free some space, or start "
                                                "it with --disk-cap-gb 2.")
    return Check("Disk space", "fail", msg, "Free at least 3 GB on the Linux disk (delete old files or runs).")


def check_data_dir(data_dir: Path) -> Check:
    p = Path(data_dir)
    if str(p.resolve()).startswith("/mnt/"):
        return Check("Data folder", "warn", f"{p} is on the Windows disk (/mnt/...), which is much slower from Linux.",
                     "Use a folder in your Linux home, e.g. windlab start --data ~/windlab-data")
    try:
        p.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=p, prefix=".doctor-"):
            pass
    except OSError as e:
        return Check("Data folder", "fail", f"WindLab cannot write to {p} ({e.strerror or e}).",
                     "Choose another folder with --data, or fix its permissions.")
    return Check("Data folder", "pass", f"{p} can be written.")


def check_port(port: int, host: str = "127.0.0.1") -> Check:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            return Check("Port", "warn", f"port {port} is already in use (perhaps WindLab is already running).",
                         f"If WindLab is not running, start it on another port: windlab start --port {port + 1}, "
                         f"then open http://localhost:{port + 1}/")
        return Check("Port", "fail", f"port {port} cannot be used ({e.strerror}).",
                     f"Start WindLab on another port: windlab start --port {port + 1}")
    finally:
        s.close()
    return Check("Port", "pass", f"port {port} is free for WindLab.")


WSLCONFIG_RESERVE_GB = 4.0  # leave Windows this much


def windows_facts(timeout: float = 20.0) -> dict:
    """What Windows itself says (only under WSL2, through PowerShell interop): the laptop's
    total RAM, its logical processors, and the current .wslconfig text. Missing pieces are
    None; nothing here is needed for a run."""
    out: dict = {"host_ram_bytes": None, "host_cores": None, "wslconfig_path": None, "wslconfig_text": None}
    if not machine.is_wsl():
        return out
    ps_dirs = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0:" + os.environ.get("PATH", "")
    ps = shutil.which("powershell.exe", path=ps_dirs)
    if ps:
        script = ("$c = Get-CimInstance Win32_ComputerSystem; "
                  "Write-Output ($c.TotalPhysicalMemory.ToString() + ' ' + $c.NumberOfLogicalProcessors + ' ' + "
                  "$env:USERPROFILE)")
        try:
            r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                               text=True, timeout=timeout)
            parts = r.stdout.strip().split(None, 2)
            if len(parts) == 3:
                out["host_ram_bytes"] = int(parts[0])
                out["host_cores"] = int(parts[1])
                win = parts[2].strip().replace("\\", "/")
                if len(win) > 2 and win[1] == ":":
                    out["wslconfig_path"] = Path(f"/mnt/{win[0].lower()}{win[2:]}") / ".wslconfig"
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if out["wslconfig_path"] is None:  # no interop: look in every Windows user folder
        for p in sorted(Path("/mnt/c/Users").glob("*/.wslconfig")) if Path("/mnt/c/Users").is_dir() else []:
            out["wslconfig_path"] = p
            break
    p = out["wslconfig_path"]
    if p is not None and p.is_file():
        with contextlib.suppress(OSError):
            out["wslconfig_text"] = p.read_text(errors="replace")
    return out


def parse_wslconfig(text: str | None) -> dict:
    """``memory`` (bytes) and ``processors`` from the [wsl2] section of a .wslconfig (None if absent)."""
    out: dict = {"memory_bytes": None, "processors": None}
    if not text:
        return out
    section = None
    units = {"": 1, "b": 1, "k": 2**10, "kb": 2**10, "m": 2**20, "mb": 2**20, "g": 2**30, "gb": 2**30, "t": 2**40,
             "tb": 2**40}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "wsl2" or "=" not in line:
            continue
        key, _, val = (x.strip() for x in line.partition("="))
        key, val = key.lower(), val.strip().lower()
        if key == "memory":
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z]*)", val)
            if m and m.group(2) in units:
                out["memory_bytes"] = int(float(m.group(1)) * units[m.group(2)])
        elif key == "processors" and val.isdigit():
            out["processors"] = int(val)
    return out


def recommended_wslconfig(host_ram_bytes: int | None, host_cores: int | None) -> tuple[str, int | None, int | None]:
    """The .wslconfig WindLab would like: RAM minus 4 GB (never under 8), every processor."""
    mem_gb = None if not host_ram_bytes else max(8, int(round(host_ram_bytes / GB)) - int(WSLCONFIG_RESERVE_GB))
    text = "[wsl2]\n" + (f"memory={mem_gb}GB\n" if mem_gb else "memory=<your RAM minus 4>GB\n") \
        + (f"processors={host_cores}\n" if host_cores else "processors=<number of processor cores>\n")
    return text, mem_gb, host_cores


def check_wslconfig(wsl: bool | None = None, facts: dict | None = None, wsl_ram_bytes: int | None = None,
                    wsl_cores: int | None = None) -> Check:
    """Under WSL2: does C:\\Users\\<you>\\.wslconfig give Linux most of the laptop? Prints the
    recommended file either way."""
    wsl = machine.is_wsl() if wsl is None else wsl
    if not wsl:
        return Check("WSL settings", "pass", "not running under WSL2 (nothing to configure).")
    facts = windows_facts() if facts is None else facts
    wsl_ram_bytes = machine.meminfo()["total"] if wsl_ram_bytes is None else wsl_ram_bytes
    wsl_cores = machine.logical_cores() if wsl_cores is None else wsl_cores
    want, mem_gb, cores = recommended_wslconfig(facts.get("host_ram_bytes"), facts.get("host_cores"))
    where = str(facts.get("wslconfig_path") or "C:\\Users\\<you>\\.wslconfig")
    fix = (f"Recommended {where} for this laptop:\n       {want.strip().replace(chr(10), chr(10) + '       ')}\n"
           "       then run  wsl --shutdown  in PowerShell and open Ubuntu again.")
    cur = parse_wslconfig(facts.get("wslconfig_text"))
    have_mem = cur["memory_bytes"] if cur["memory_bytes"] else wsl_ram_bytes
    have_cores = cur["processors"] if cur["processors"] else wsl_cores
    if facts.get("wslconfig_text") is None:
        state = f"no .wslconfig found; Linux currently gets {wsl_ram_bytes / GB:.1f} GB and {wsl_cores} processors."
    else:
        state = (f"{where} gives Linux "
                 f"{'%.0f GB' % (cur['memory_bytes'] / GB) if cur['memory_bytes'] else 'the default memory'} and "
                 f"{cur['processors'] if cur['processors'] else 'the default number of'} processors.")
    if mem_gb is None and cores is None:  # Windows could not be asked: only report what Linux sees
        if facts.get("wslconfig_text") is None:
            return Check("WSL settings", "warn", state, fix)
        return Check("WSL settings", "pass", state + " (Windows could not be asked what the laptop has.)")
    ok_mem = mem_gb is None or have_mem >= (mem_gb - 1) * GB
    ok_cores = cores is None or have_cores >= cores
    if ok_mem and ok_cores:
        return Check("WSL settings", "pass", state + " That matches what WindLab recommends.")
    short = []
    if not ok_mem:
        short.append(f"memory could be {mem_gb} GB")
    if not ok_cores:
        short.append(f"processors could be {cores}")
    return Check("WSL settings", "warn", state + " " + "; ".join(short) + ".", fix)


def check_tools() -> Check:
    if shutil.which("prlimit", path="/usr/bin:/bin") is None:
        return Check("System tools", "fail", "the 'prlimit' tool is missing (needed to check uploaded files safely).",
                     "sudo apt install util-linux")
    return Check("System tools", "pass", "prlimit found.")


def check_step_import(probe: Callable[[], tuple[bool, str]] | None = None) -> Check:
    """Can WindLab read STEP files (the optional OpenCASCADE extra)? A warning, never a failure:
    STL upload and the built-in shapes work without it."""
    from windlab.geometry import step

    ok, detail = (probe or step.probe)()
    if ok:
        return Check("STEP import", "pass", f"available ({detail}).")
    return Check("STEP import", "warn", f"not available: {detail}. STL upload still works.",
                 f"To read SolidWorks STEP files, run  {step.INSTALL_HINT}  (with the WindLab environment active), "
                 "then start WindLab again.")


def check_test_run(data_dir: Path, n_procs: int) -> Check:
    """Mesh and solve a tiny case with the real pipeline (internal 'check' preset)."""
    from windlab.case_builder import FlowConditions, GeometryInput, Preset, ReferenceValues, build_case
    from windlab.geometry.naca import naca_wing
    from windlab.runner import RunError, run_case

    work = Path(tempfile.mkdtemp(prefix="doctor-", dir=Path(data_dir)))
    t0 = time.monotonic()
    try:
        w = naca_wing("0012", chord=0.3, span=0.6)
        stl = work / "wing.stl"
        w.write_stl(stl)
        g = GeometryInput(stl_path=stl, bounds_min=tuple(w.vertices.min(0)), bounds_max=tuple(w.vertices.max(0)))
        case = build_case(g, FlowConditions(airspeed=20, alpha_deg=4), ReferenceValues(area=0.18, length=0.3),
                          preset=Preset.CHECK, n_procs=n_procs, runs_root=work)
        rep = run_case(case.path)
        cl = rep.coefficients.get("cl")
        secs = time.monotonic() - t0
        if cl is None or not (0.0 < cl < 0.5):
            return Check("Test run", "fail", f"the tiny test case ran but gave an odd lift coefficient ({cl}).",
                         "Please report this, with the output of windlab doctor.")
        return Check("Test run", "pass", f"a tiny case ({rep.mesh.get('cells', 0):,d} cells, {n_procs} cores) "
                                         f"meshed and solved in {secs:.0f} s.")
    except RunError as e:
        log = f" Log: {e.log}" if e.log else ""
        return Check("Test run", "fail", f"the tiny test case stopped in {e.stage}.{log}",
                     "Check the OpenFOAM line above; if it passed, please report this with the log file.")
    except Exception as e:  # noqa: BLE001
        return Check("Test run", "fail", f"the tiny test case could not be set up ({e}).",
                     "Please report this, with the output of windlab doctor.")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- run


def run_checks(data_dir: Path, port: int, n_procs: int, test_run: bool = True,
               out: Callable[[str], None] = print) -> list[Check]:
    from windlab import __version__

    out(f"WindLab {__version__} doctor - checking this computer")
    info = machine.summary()
    out(f"  {info['os']} ({'WSL2' if info['wsl'] else 'Linux'}), {info['cpu']}\n")
    checks: list[Check] = []

    def run(fn, *a):
        c = fn(*a)
        checks.append(c)
        out(c.line())
        return c

    foam = run(check_openfoam)
    run(check_cores)
    run(check_memory)
    run(check_wslconfig)
    run(check_disk, data_dir)
    data_ok = run(check_data_dir, data_dir)
    run(check_port, port)
    run(check_tools)
    run(check_step_import)
    if test_run:
        if foam.status == "fail" or data_ok.status == "fail":
            c = Check("Test run", "fail", "skipped because of the problem(s) above.", "Fix those first.")
            checks.append(c)
            out(c.line())
        else:
            out("       (running a tiny test case, about 30 seconds...)")
            run(check_test_run, data_dir, n_procs)
    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    out("")
    if fails:
        out(f"{fails} problem(s) must be fixed before WindLab can run. See the -> lines above.")
    elif warns:
        out(f"WindLab should work. {warns} warning(s) above are worth a look.")
    else:
        out("All good: start WindLab with  windlab start")
    return checks


def default_test_procs() -> int:
    return max(1, min(2, os.cpu_count() or 1))
