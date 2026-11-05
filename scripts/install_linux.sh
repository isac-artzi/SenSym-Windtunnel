#!/usr/bin/env bash
# WindLab installer for Ubuntu 24.04 (native or on WSL2; 22.04 with Python 3.12): docs/INSTALL.md.
#
#   From a clone (recommended):
#       git clone https://github.com/isac-artzi/SenSym-Windtunnel.git windlab
#       bash windlab/scripts/install_linux.sh
#   Or without cloning first (it clones for you):
#       bash <(curl -fsSL https://raw.githubusercontent.com/isac-artzi/SenSym-Windtunnel/main/scripts/install_linux.sh)
#   (scripts/install_wsl.sh is the same script under its old name.)
#
# What it does (and skips what is already done, so you can run it again):
#   1. apt packages: curl, git, python3-venv, util-linux, libgl1 (for STEP import)
#   2. OpenFOAM v2606 from dl.openfoam.com (openfoam2606-default)
#   3. the WindLab repository in ~/windlab (git clone, or git pull if it is there; or the folder this
#      script was run from)
#   4. a Python environment in ~/windlab-venv with WindLab installed in it (with the STEP import
#      extra, pip install ".[step]"), and ~/windlab-venv/bin on your PATH for new terminal windows
#   5. windlab doctor
# It never deletes anything and only uses sudo for apt. Set WINDLAB_REPO_URL, WINDLAB_DIR or
# WINDLAB_VENV in the environment to change the defaults. No GitHub account or token is needed.

set -euo pipefail

REPO_URL="${WINDLAB_REPO_URL:-https://github.com/isac-artzi/SenSym-Windtunnel.git}"
WINDLAB_DIR="${WINDLAB_DIR:-$HOME/windlab}"
VENV="${WINDLAB_VENV:-$HOME/windlab-venv}"
FOAM_BASHRC="/usr/lib/openfoam/openfoam2606/etc/bashrc"
STEPS=5

say()  { printf '\n==> %s\n' "$*"; }
ok()   { printf '    [ok] %s\n' "$*"; }
skip() { printf '    [already done] %s\n' "$*"; }
note() { printf '    [note] %s\n' "$*"; }
fail() { printf '\n*** %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 0. checks
say "Checking this computer"
if [ "$(id -u)" = "0" ]; then
    fail "Please run this as your normal user, not as root (no 'sudo bash ...')."
fi
ON_WSL=0
if grep -qi microsoft /proc/version 2>/dev/null; then
    ON_WSL=1
    ok "Ubuntu on WSL2 (Windows)"
else
    ok "Linux (not WSL)"
fi
rel=""
if command -v lsb_release >/dev/null 2>&1; then
    rel="$(lsb_release -rs 2>/dev/null || true)"
elif [ -f /etc/os-release ]; then
    rel="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
fi
if [ -f /etc/os-release ] && ! grep -qi '^ID=ubuntu' /etc/os-release; then
    fail "This is not Ubuntu. WindLab's installer supports Ubuntu 24.04 (and 22.04); on other distributions use the Docker route in docs/INSTALL.md."
fi
case "${rel%%.*}" in
    24) ok "Ubuntu $rel" ;;
    22) note "Ubuntu $rel: WindLab needs Python 3.12 (see below); Ubuntu 24.04 is the tested version." ;;
    *)
        if [ "$ON_WSL" = "1" ]; then
            fail "This Ubuntu is version '$rel'; WindLab needs 24.04. In PowerShell run  wsl -l -v  and  wsl --install -d Ubuntu-24.04  (see docs/INSTALL.md, Windows)."
        fi
        fail "This Ubuntu is version '$rel'; WindLab needs 24.04 (22.04 works with Python 3.12). Otherwise use the Docker route in docs/INSTALL.md." ;;
esac
if ! command -v sudo >/dev/null 2>&1; then
    fail "sudo is missing; this Ubuntu is unusual. Ask your instructor."
fi
if ! curl -fsSI --max-time 20 https://dl.openfoam.com/ >/dev/null 2>&1; then
    fail "No internet connection (could not reach dl.openfoam.com). Connect and run this again; on a campus network that blocks downloads, try another network."
fi
ok "internet connection"
# Python 3.12 or newer (Ubuntu 24.04 has it; 22.04 needs the deadsnakes packages)
PY=""
for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        PY="$(command -v "$candidate")"; break
    fi
done
if [ -z "$PY" ]; then
    printf '    Python 3.12 or newer was not found. On Ubuntu 22.04 install it first:\n'
    printf '        sudo add-apt-repository ppa:deadsnakes/ppa\n'
    printf '        sudo apt install -y python3.12 python3.12-venv\n'
    fail "then run this script again (or use Ubuntu 24.04 / the Docker route, docs/INSTALL.md)."
fi
PYV="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
ok "Python $PYV ($PY)"

# ---------------------------------------------------------------- 1. apt packages
say "Step 1 of $STEPS: Ubuntu packages (you may be asked for your password)"
need=()
for p in curl git python3-venv "python${PYV}-venv" python3-pip util-linux ca-certificates libgl1; do
    if ! dpkg -s "$p" >/dev/null 2>&1; then need+=("$p"); fi
done
if [ "${#need[@]}" -gt 0 ]; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}"
    ok "installed: ${need[*]}"
else
    skip "curl, git, python3-venv, util-linux, libgl1"
fi

# ---------------------------------------------------------------- 2. OpenFOAM
say "Step 2 of $STEPS: OpenFOAM v2606 (a few hundred MB; this is the slow part)"
if [ -f "$FOAM_BASHRC" ]; then
    skip "OpenFOAM v2606 is installed ($FOAM_BASHRC)"
else
    if [ ! -f /etc/apt/sources.list.d/openfoam.list ] && ! ls /etc/apt/sources.list.d/ 2>/dev/null | grep -qi openfoam; then
        curl -fsSL https://dl.openfoam.com/add-debian-repo.sh | sudo bash
    fi
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openfoam2606-default
    [ -f "$FOAM_BASHRC" ] || fail "OpenFOAM did not install ($FOAM_BASHRC is missing). Copy the messages above and ask for help (GitHub Issues)."
    ok "OpenFOAM v2606 installed"
fi

# ---------------------------------------------------------------- 3. the repository
say "Step 3 of $STEPS: WindLab's files in $WINDLAB_DIR"
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$here" ] && [ -f "$here/pyproject.toml" ] && [ ! -d "$WINDLAB_DIR/.git" ]; then
    WINDLAB_DIR="$here"   # running from a checkout somewhere else: use that one
    ok "using the checkout this script came from: $WINDLAB_DIR"
fi
if [ -d "$WINDLAB_DIR/.git" ]; then
    if git -C "$WINDLAB_DIR" diff --quiet 2>/dev/null; then
        if git -C "$WINDLAB_DIR" pull --ff-only -q; then
            ok "updated $WINDLAB_DIR to the latest version"
        else
            note "git pull did not succeed (no connection?); using the copy you have."
        fi
    else
        skip "$WINDLAB_DIR has local changes; left as it is"
    fi
elif [ -f "$WINDLAB_DIR/pyproject.toml" ]; then
    ok "using the WindLab folder at $WINDLAB_DIR (not a git clone: update it by downloading a new ZIP)"
else
    if ! git clone -q "$REPO_URL" "$WINDLAB_DIR"; then
        printf '\n    The download from GitHub failed. Check the internet connection and try again; if your network\n'
        printf '    blocks GitHub, download the ZIP from %s on another network,\n' "${REPO_URL%.git}"
        printf '    unpack it as %s and run this script again.\n' "$WINDLAB_DIR"
        fail "Could not clone $REPO_URL"
    fi
    ok "downloaded WindLab to $WINDLAB_DIR"
fi

# ---------------------------------------------------------------- 4. the Python environment
say "Step 4 of $STEPS: Python environment in $VENV"
if [ -x "$VENV/bin/python" ]; then
    skip "$VENV exists"
else
    "$PY" -m venv "$VENV"
    ok "created $VENV with $PY"
fi
"$VENV/bin/python" -m pip install -q --upgrade pip
# ".[step]" adds STEP import (OpenCASCADE, about 100 MB); if that download fails, WindLab still
# installs without it and reads STL files only (windlab doctor says so).
if "$VENV/bin/python" -m pip install -q "$WINDLAB_DIR[step]"; then
    ok "WindLab $("$VENV/bin/windlab" --version 2>/dev/null | sed 's/^WindLab //') installed in $VENV (with STEP import)"
else
    note "the STEP import extra could not be installed; installing WindLab without it (STL upload works)."
    "$VENV/bin/python" -m pip install -q "$WINDLAB_DIR"
    ok "WindLab $("$VENV/bin/windlab" --version 2>/dev/null | sed 's/^WindLab //') installed in $VENV (STL only)"
fi
marker="# WindLab: its commands in every new terminal window"
if ! grep -qs "WindLab: its commands in every new" "$HOME/.bashrc"; then
    printf '\n%s\nexport PATH="%s/bin:$PATH"\n' "$marker" "$VENV" >> "$HOME/.bashrc"
    ok "added $VENV/bin to the PATH in ~/.bashrc (new terminal windows know 'windlab')"
else
    skip "~/.bashrc already puts windlab on the PATH"
fi

# ---------------------------------------------------------------- 5. doctor
say "Step 5 of $STEPS: windlab doctor (meshes and solves a tiny test case, about 30 s)"
set +e
"$VENV/bin/windlab" doctor
rc=$?
set -e

printf '\n============================================================\n'
if [ "$rc" -eq 0 ]; then
    printf 'WindLab is installed. Start it with:\n\n    windlab start\n\n'
    if [ "$ON_WSL" = "1" ]; then
        printf 'then open http://localhost:8080/ in your Windows browser.\n'
    else
        printf 'then open http://localhost:8080/ in your browser.\n'
    fi
    printf '(In this window first run:  export PATH="%s/bin:$PATH"  - new windows have it already.)\n' "$VENV"
    printf 'The first start shows the educational-use notice (DISCLAIMER.md) and asks you to type yes.\n'
else
    printf 'WindLab is installed but  windlab doctor  found problems (the FAIL lines above).\n'
    printf 'Fix them and run  windlab doctor  again; see "Troubleshooting" in docs/INSTALL.md.\n'
fi
printf 'Later, to get the newest WindLab:  windlab update\n'
exit "$rc"
