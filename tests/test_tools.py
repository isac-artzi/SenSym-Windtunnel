"""windlab doctor, windlab benchmark, machine facts, and the Windows preflight script."""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest

from windlab import benchmark as bm
from windlab import cli, doctor, machine
from windlab.runner import RunReport, StageResult

GB = 2**30


# --------------------------------------------------------------------------- machine


def test_machine_summary_is_sane():
    s = machine.summary()
    assert s["logical_cores"] >= 1 and 1 <= s["physical_cores"] <= s["logical_cores"]
    assert s["ram_gb"] > 0 and isinstance(s["wsl"], bool) and s["os"]


def test_free_disk_of_a_folder_that_does_not_exist_yet(tmp_path):
    assert machine.free_disk(tmp_path / "not" / "yet") > 0


# --------------------------------------------------------------------------- doctor checks


@pytest.mark.parametrize("physical, status", [(8, "pass"), (4, "pass"), (2, "warn"), (1, "fail")])
def test_cores(physical, status):
    c = doctor.check_cores(logical=physical * 2, physical=physical, wsl=False)
    assert c.status == status
    assert f"WindLab will use {machine.solver_cores(physical, physical * 2, False)} core" in c.message


def test_cores_under_wsl_counts_the_virtual_processors():
    # processors=8 in .wslconfig shown as 4 cores x 2 threads: still 8 usable processors
    c = doctor.check_cores(logical=8, physical=4, wsl=True)
    assert c.status == "pass" and "will use 7 cores" in c.message


@pytest.mark.parametrize("physical, logical, wsl, ranks", [
    (14, 28, False, 8),  # a 14-core Xeon workstation on Linux proper: capped at 8
    (16, 16, False, 8),  # the dev container
    (8, 8, True, 7),  # WSL with processors=8, shown as 8 x 1
    (4, 8, True, 7),  # WSL with processors=8, shown as 4 x 2
    (6, 12, False, 5),  # a 6-core laptop with hyper-threading: physical cores minus one
    (4, 8, False, 3),
    (2, 4, False, 1),
    (2, 2, True, 1),
    (1, 1, False, 1),
])
def test_solver_cores(physical, logical, wsl, ranks):
    assert machine.solver_cores(physical, logical, wsl) == ranks


def test_solver_cores_defaults_to_this_machine():
    n = machine.solver_cores()
    assert 1 <= n <= machine.MAX_SOLVER_CORES and n <= max(machine.logical_cores(), machine.physical_cores())
    assert cli.default_procs() == n and bm.default_np() == n and machine.summary()["solver_cores"] == n


@pytest.mark.parametrize("total, avail, status", [(12, 8, "pass"), (12, 1, "warn"), (8, 6, "warn"), (4, 3, "fail")])
def test_memory(total, avail, status):
    c = doctor.check_memory(total=total * GB, available=avail * GB)
    assert c.status == status and f"{total:.1f} GB" in c.message
    if status != "pass":
        assert c.fix


@pytest.mark.parametrize("free, status", [(50, "pass"), (5, "warn"), (1, "fail")])
def test_disk(tmp_path, free, status):
    assert doctor.check_disk(tmp_path, free=free * GB).status == status


def test_port_free_and_taken():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen()
    try:
        c = doctor.check_port(port)
        assert c.status == "warn" and f"--port {port + 1}" in c.fix
    finally:
        s.close()
    assert doctor.check_port(port).status == "pass"


def test_data_dir(tmp_path):
    assert doctor.check_data_dir(tmp_path / "d").status == "pass"
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert doctor.check_data_dir(ro).status in ("fail", "pass")  # pass only when running as root
    finally:
        ro.chmod(0o700)


def test_openfoam_missing(monkeypatch, tmp_path):
    from windlab import runner

    monkeypatch.setattr(runner, "FOAM_BASHRC", str(tmp_path / "nope" / "bashrc"))
    c = doctor.check_openfoam()
    assert c.status == "fail" and "openfoam2606-default" in c.fix


def test_lines_are_plain():
    c = doctor.Check("Memory", "warn", "4 GB.", "Add more.")
    assert c.line() == "[WARN] Memory: 4 GB.\n       -> Add more."
    assert doctor.Check("Memory", "pass", "16 GB.", "ignored").line() == "[PASS] Memory: 16 GB."


def test_run_checks_skips_test_run_when_openfoam_is_missing(monkeypatch, tmp_path):
    from windlab import runner

    monkeypatch.setattr(runner, "FOAM_BASHRC", str(tmp_path / "nope"))
    lines = []
    checks = doctor.run_checks(tmp_path, 1, 1, test_run=True, out=lines.append)
    names = [c.name for c in checks]
    assert names[0] == "OpenFOAM" and names[-1] == "Test run" and checks[-1].status == "fail"
    assert "skipped" in checks[-1].message and any("must be fixed" in x for x in lines)


def test_doctor_cli_exit_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda *a, **k: [doctor.Check("x", "warn", "y")])
    assert cli.main(["doctor", "--data", str(tmp_path), "--no-test-run"]) == 0
    monkeypatch.setattr(doctor, "run_checks", lambda *a, **k: [doctor.Check("x", "fail", "y")])
    assert cli.main(["doctor", "--data", str(tmp_path)]) == 1
    assert cli.main(["doctor", "--port", "0", "--data", str(tmp_path)]) == 2


# --------------------------------------------------------------------------- .wslconfig (task 010, step 5e)


def test_parse_wslconfig():
    text = "# comment\n[wsl2]\nmemory=12GB   # twelve\nprocessors = 8\n[experimental]\nmemory=1GB\n"
    assert doctor.parse_wslconfig(text) == {"memory_bytes": 12 * GB, "processors": 8}
    assert doctor.parse_wslconfig("[wsl2]\nmemory=8192MB\n") == {"memory_bytes": 8 * GB, "processors": None}
    assert doctor.parse_wslconfig(None) == {"memory_bytes": None, "processors": None}
    assert doctor.parse_wslconfig("[wsl2]\nmemory=lots\nprocessors=many\n")["memory_bytes"] is None


def test_recommended_wslconfig():
    text, mem, cores = doctor.recommended_wslconfig(32 * GB, 16)
    assert mem == 28 and cores == 16 and text == "[wsl2]\nmemory=28GB\nprocessors=16\n"
    assert doctor.recommended_wslconfig(8 * GB, 4)[1] == 8  # never below 8 GB
    assert "<your RAM minus 4>" in doctor.recommended_wslconfig(None, None)[0]


def test_wslconfig_check_matches_and_mismatches():
    facts = {"host_ram_bytes": 32 * GB, "host_cores": 28, "wslconfig_path": "C:\\Users\\student\\.wslconfig",
             "wslconfig_text": "[wsl2]\nmemory=12GB\nprocessors=8\n"}
    c = doctor.check_wslconfig(wsl=True, facts=facts, wsl_ram_bytes=12 * GB, wsl_cores=8)
    assert c.status == "warn" and "memory could be 28 GB" in c.message and "processors could be 28" in c.message
    assert "memory=28GB" in c.fix and "processors=28" in c.fix and "wsl --shutdown" in c.fix
    good = {**facts, "wslconfig_text": "[wsl2]\nmemory=28GB\nprocessors=28\n"}
    c = doctor.check_wslconfig(wsl=True, facts=good, wsl_ram_bytes=28 * GB, wsl_cores=28)
    assert c.status == "pass" and "matches" in c.message
    none = {**facts, "wslconfig_path": None, "wslconfig_text": None}
    c = doctor.check_wslconfig(wsl=True, facts=none, wsl_ram_bytes=16 * GB, wsl_cores=14)
    assert c.status == "warn" and "no .wslconfig" in c.message and "C:\\Users\\<you>\\.wslconfig" in c.fix
    # Windows could not be asked: only what Linux sees, and a nudge when there is no file at all
    blind = {"host_ram_bytes": None, "host_cores": None, "wslconfig_path": None, "wslconfig_text": None}
    assert doctor.check_wslconfig(wsl=True, facts=blind, wsl_ram_bytes=8 * GB, wsl_cores=4).status == "warn"
    assert doctor.check_wslconfig(wsl=True, facts={**blind, "wslconfig_text": "[wsl2]\nmemory=12GB\n"},
                                  wsl_ram_bytes=12 * GB, wsl_cores=4).status == "pass"
    assert doctor.check_wslconfig(wsl=False).status == "pass"


def test_windows_facts_outside_wsl():
    f = doctor.windows_facts() if not machine.is_wsl() else {"host_ram_bytes": None}
    assert f["host_ram_bytes"] is None or f["host_ram_bytes"] > 0


# --------------------------------------------------------------------------- windlab update (task 010, step 5d)


def test_update_runs_git_pull_then_pip_install(tmp_path, capsys):
    repo = tmp_path / "windlab"
    (repo / ".git").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\n")
    calls = []

    class R:
        returncode, stdout = 0, "0.1.1"

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return R()

    args = cli.build_parser().parse_args(["update", "--repo", str(repo)])
    assert cli.update(args, run=fake_run) == 0
    assert calls[0][:3] == ["git", "-C", str(repo.resolve())] and "pull" in calls[0]
    assert calls[1][1:4] == ["-m", "pip", "install"] and calls[1][-1] == str(repo.resolve())
    out = capsys.readouterr().out
    assert "git pull" in out and "WindLab 0.1.1 is installed" in out


def test_update_explains_a_failed_pull(tmp_path, capsys):
    repo = tmp_path / "windlab"
    (repo / ".git").mkdir(parents=True)

    class Bad:
        returncode, stdout = 1, ""

    args = cli.build_parser().parse_args(["update", "--repo", str(repo)])
    assert cli.update(args, run=lambda cmd, **kw: Bad()) == 1
    err = capsys.readouterr().err
    assert "git pull did not succeed" in err and "git stash" in err and "gh auth" not in err
    args = cli.build_parser().parse_args(["update", "--repo", str(tmp_path / "nowhere")])
    assert cli.update(args, run=lambda cmd, **kw: Bad()) == 2
    assert "could not find the WindLab checkout" in capsys.readouterr().err


def test_repo_dir_finds_this_checkout(monkeypatch):
    monkeypatch.delenv("WINDLAB_REPO", raising=False)
    assert cli.repo_dir() == Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- benchmark


def fake_runner(case_dir, progress=None):
    meta = json.loads((Path(case_dir) / "windlab_case.json").read_text())
    progress("stage", {"stage": "snappyHexMesh"})
    rep = RunReport(run_id=meta["run_id"], case_dir=str(case_dir), n_procs=meta["n_procs"])
    rep.stages = [StageResult("blockMesh", 0.5, 100, 0), StageResult("snappyHexMesh", 11.0, 900, 0),
                  StageResult("potentialFoam", 1.0, 300, 0), StageResult("simpleFoam", 66.0, 1000, 0)]
    rep.mesh = {"cells": 372_613, "mesh_ok": True}
    rep.iterations, rep.converged = 397, True
    rep.coefficients = {"cl": 0.452, "cd": 0.0559, "cm": 0.016}
    return rep


def test_benchmark_result_and_files(tmp_path):
    lines = []
    b = bm.run_benchmark(tmp_path, 3, runner=fake_runner, out=lines.append)
    assert b["case"]["n_procs"] in (1, 2, 3) and b["case"]["alpha_deg"] == 10 and b["case"]["preset"] == "quick"
    assert b["cells"] == 372_613 and b["cl"] == 0.452 and b["iterations"] == 397
    assert b["stage_seconds"]["snappyHexMesh"] == 11.0 and b["mesh_seconds"] == 11.5 and b["solver_seconds"] == 66
    assert b["seconds_per_iteration"] == pytest.approx(66 / 397, abs=1e-4) and b["peak_memory_mb"] == 1000
    assert "not yet validated" in b["label"] and b["machine"]["physical_cores"] >= 1
    assert not list(tmp_path.glob("benchmark-*"))  # the temporary case is removed
    text = bm.text_summary(b)
    assert "372,613 cells" in text and "CL 0.4520" in text and "397 iterations" in text
    js, txt = bm.save(b, tmp_path / "out.json")
    assert json.loads(js.read_text())["cl"] == 0.452 and txt.read_text().startswith("WindLab benchmark")
    assert any("snappyHexMesh" in x for x in lines)


def test_benchmark_cli(monkeypatch, tmp_path, capsys):
    out = tmp_path / "b.json"
    assert cli.benchmark(cli.build_parser().parse_args(
        ["benchmark", "--np", "2", "--data", str(tmp_path), "--out", str(out)]), runner=fake_runner) == 0
    assert out.is_file() and out.with_suffix(".txt").is_file()
    assert "Saved" in capsys.readouterr().out
    # bad values are refused before anything runs (never call main() with values that would start a real run)
    assert cli.main(["benchmark", "--np", "500", "--data", str(tmp_path)]) == 2
    assert cli.main(["benchmark", "--np", "2", "--alpha", "45", "--data", str(tmp_path)]) == 2


# --------------------------------------------------------------------------- Windows preflight (static checks)

PS1 = (Path(__file__).resolve().parents[1] / "scripts" / "windows" / "preflight.ps1").read_text()


def test_preflight_covers_the_checks():
    for word in ("Win32_OperatingSystem", "TotalPhysicalMemory", "NumberOfLogicalProcessors",
                 "VirtualizationFirmwareEnabled", "HypervisorPresent", "wsl.exe", "FreeSpace", "UNTESTED ON WINDOWS"):
        assert word in PS1, word


def test_preflight_is_read_only_and_needs_no_admin():
    for bad in ("Set-ItemProperty", "Enable-WindowsOptionalFeature", "dism", "Start-Process", "-Verb RunAs",
                "Invoke-WebRequest", "Remove-Item", "wsl --install", "bcdedit"):
        assert bad.lower() not in PS1.lower(), bad


def test_preflight_braces_balance_and_ps51_syntax():
    code = re.sub(r"<#.*?#>", "", PS1, flags=re.S)
    code = re.sub(r"'[^'\n]*'", "''", code)
    code = re.sub(r'"[^"\n]*"', '""', code)
    for a, b in ("{}", "()", "[]"):
        assert code.count(a) == code.count(b), (a, b)
    assert "??" not in code and "?." not in code  # PowerShell 7 only; Windows 11 ships 5.1
