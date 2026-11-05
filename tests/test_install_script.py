"""The install and Docker scripts: static checks (they cannot run here - they need sudo, Docker and the internet)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_linux.sh"
WRAPPER = ROOT / "scripts" / "install_wsl.sh"
DOCKER = ROOT / "scripts" / "run_docker.sh"
COMPOSE = ROOT / "dev" / "compose.yaml"
TEXT = SCRIPT.read_text()


def _parses(path: Path) -> None:
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = path.read_text()
    assert text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in text


def test_scripts_parse_and_are_strict():
    for path in (SCRIPT, WRAPPER, DOCKER):
        _parses(path)


def test_installer_covers_the_steps_and_both_platforms():
    for needle in ("add-debian-repo.sh", "openfoam2606-default", "python3-venv", "util-linux", "libgl1", "git clone",
                   '"$PY" -m venv', "pip install -q", "windlab\" doctor", "lsb_release", "wsl -l -v", "/proc/version",
                   "ON_WSL", "deadsnakes", "sys.version_info >= (3, 12)", "docs/INSTALL.md", "DISCLAIMER.md",
                   "https://github.com/isac-artzi/SenSym-Windtunnel.git"):
        assert needle in TEXT, needle
    # a public repository: no GitHub sign-in, no tokens (the header comment may say so)
    code = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
    assert "gh auth" not in code and " gh " not in code and "token" not in code.lower()
    assert "ZIP" in TEXT  # the no-git fallback is explained


def test_installer_is_idempotent_and_careful():
    # every step checks before it acts
    for guard in ('[ -f "$FOAM_BASHRC" ]', '[ -d "$WINDLAB_DIR/.git" ]', '[ -x "$VENV/bin/python" ]',
                  'grep -qs "WindLab: its commands in every new"', "dpkg -s"):
        assert guard in TEXT, guard
    assert "rm -rf" not in TEXT and "rm -r" not in TEXT
    assert 'id -u' in TEXT  # refuses to run as root
    # sudo only for apt and the OpenFOAM repository script (the deadsnakes lines are printed advice, not run)
    for line in TEXT.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "printf")):
            continue
        if re.search(r"(^|\|\s*)sudo\b", stripped):
            assert "apt-get" in line or "add-debian-repo.sh" in line, line
    # GNU bash, no bashisms the guide does not expect from students
    assert not re.search(r"\bbrew\b|apt install -y\s*$", TEXT)


def test_wsl_wrapper_runs_the_linux_installer():
    text = WRAPPER.read_text()
    assert 'exec bash "$here/install_linux.sh" "$@"' in text
    assert "install_linux.sh" in text and "curl -fsSL" in text  # also when run from a pipe


def test_docker_script_and_compose_start_windlab_on_8080():
    text = DOCKER.read_text()
    for needle in ("docker compose -f dev/compose.yaml", "DISCLAIMER.md", "WINDLAB_ACCEPT_DISCLAIMER",
                   "--accept-disclaimer", "docker info", "build windlab", "up windlab", "http://localhost:$PORT/",
                   "windlab doctor", '.windlab/accepted', "windlab/disclaimer.py"):
        assert needle in text, needle
    assert "rm -rf" not in text
    compose = yaml.safe_load(COMPOSE.read_text())
    w = compose["services"]["windlab"]
    assert w["command"] == "windlab start --host 0.0.0.0 --port 8080"
    assert w["ports"] == ["127.0.0.1:${WINDLAB_PORT:-8080}:8080"]
    assert "runs:/runs" in w["volumes"] and "..:/work" in w["volumes"]
    assert w["build"] == {"context": "."} and "runs" in compose["volumes"]
    assert "WINDLAB_ACCEPT_DISCLAIMER" in w["environment"]
    dev = compose["services"]["dev"]
    assert dev["profiles"] == ["dev"]  # plain `up` does not start the maintainer shell
    dockerfile = (ROOT / "dev" / "Dockerfile").read_text()
    assert "FROM ubuntu:24.04" in dockerfile and "openfoam${OPENFOAM_VERSION}-default" in dockerfile
    assert 'ARG CLAUDE_CODE=0' in dockerfile and 'if [ "$CLAUDE_CODE" = "1" ]' in dockerfile  # off by default
    assert "EXPOSE 8080" in dockerfile and "cadquery-ocp" in dockerfile
