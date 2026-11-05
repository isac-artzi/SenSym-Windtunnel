"""Facts about the computer WindLab runs on (Linux / WSL2): cores, memory, disk, OS."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def logical_cores() -> int:
    return os.cpu_count() or 1


def physical_cores() -> int:
    """Physical cores (hyper-threads not counted), from lscpu, else /proc/cpuinfo."""
    lscpu = shutil.which("lscpu", path="/usr/bin:/bin")
    if lscpu:
        try:
            out = subprocess.run([lscpu, "-p=CORE,SOCKET"], capture_output=True, text=True, timeout=10).stdout
            cores = {tuple(line.split(",")) for line in out.splitlines() if line and not line.startswith("#")}
            if cores:
                return len(cores)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return logical_cores()
    pairs, phys = set(), None
    for line in text.splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k == "physical id":
            phys = v
        elif k == "core id":
            pairs.add((phys, v))
    return len(pairs) or logical_cores()


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if k.strip() in ("model name", "Model", "Hardware"):
                return v.strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def meminfo() -> dict[str, int]:
    """MemTotal / MemAvailable in bytes (0 if unknown)."""
    out = {"total": 0, "available": 0}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("MemTotal", "MemAvailable"):
                out["total" if k == "MemTotal" else "available"] = int(v.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def free_disk(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


MAX_SOLVER_CORES = 8  # the Quick mesh barely scales beyond this


def solver_cores(physical: int | None = None, logical: int | None = None, wsl: bool | None = None,
                 cap: int = MAX_SOLVER_CORES) -> int:
    """How many MPI ranks a run should use by default: the cores minus one, at most ``cap``.

    On Linux proper the physical cores count (a second hyper-thread on a core does not
    help a memory-bound solver). Under WSL2 the topology Linux sees is synthetic: with
    ``processors=8`` in .wslconfig lscpu may say 8 cores x 1 thread or 4 cores x 2
    threads, while Windows actually schedules those 8 virtual processors across the
    laptop's real cores. There the logical count is the honest one (the 14-core Xeon test
    workstation, 2026-11-03: 7 ranks 140 s against 197 s on 4).
    """
    physical = physical_cores() if physical is None else int(physical)
    logical = logical_cores() if logical is None else int(logical)
    wsl = is_wsl() if wsl is None else bool(wsl)
    usable = max(logical, physical) if wsl else max(1, min(physical, logical))
    return max(1, min(cap, usable - 1))


def summary() -> dict:
    mem = meminfo()
    return {
        "cpu": cpu_model(),
        "logical_cores": logical_cores(),
        "physical_cores": physical_cores(),
        "ram_gb": round(mem["total"] / 2**30, 1),
        "ram_available_gb": round(mem["available"] / 2**30, 1),
        "os": os_name(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "wsl": is_wsl(),
        "solver_cores": solver_cores(),
        "python": platform.python_version(),
    }
