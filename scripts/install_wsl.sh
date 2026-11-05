#!/usr/bin/env bash
# WindLab installer for Ubuntu on WSL2 - kept under its old name; the script itself is
# scripts/install_linux.sh (it detects WSL2 by itself). Both ways of running it still work:
#   bash ~/windlab/scripts/install_wsl.sh
#   bash <(curl -fsSL https://raw.githubusercontent.com/isac-artzi/SenSym-Windtunnel/main/scripts/install_wsl.sh)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/install_linux.sh" ]; then
    exec bash "$here/install_linux.sh" "$@"
fi
# run from a pipe (bash <(curl ...)): fetch the real script the same way
RAW="${WINDLAB_RAW_URL:-https://raw.githubusercontent.com/isac-artzi/SenSym-Windtunnel/main/scripts}"
exec bash <(curl -fsSL "$RAW/install_linux.sh") "$@"
