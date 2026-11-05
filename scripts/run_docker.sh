#!/usr/bin/env bash
# Run WindLab in Docker (macOS with Docker Desktop, or any Linux with Docker): docs/INSTALL.md.
#
#   scripts/run_docker.sh              build the image if needed (15-25 min the first time), start WindLab,
#                                      open http://localhost:8080/ in your browser; Ctrl+C stops it
#   scripts/run_docker.sh --build      rebuild the image (after a Docker or OpenFOAM update)
#   scripts/run_docker.sh shell        a Linux shell inside the running container (OpenFOAM loaded)
#   scripts/run_docker.sh doctor       run  windlab doctor  inside the container
#   scripts/run_docker.sh down         stop and remove the container (the runs volume stays)
#   scripts/run_docker.sh --accept-disclaimer   skip the question below (scripts, CI); or set
#                                               WINDLAB_ACCEPT_DISCLAIMER=1
#
# Before the first start it shows DISCLAIMER.md and asks you to type "yes", like  windlab start  does
# on Linux; the answer is remembered in ~/.windlab/accepted (with the notice's version). WINDLAB_PORT=8081
# changes the port on this computer. It needs nothing but Docker; everything else lives in the image.

set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -p windlab -f dev/compose.yaml)   # own project: own runs volume
ACCEPT_FILE="$HOME/.windlab/accepted"
VERSION="$(sed -n 's/^VERSION = "\([^"]*\)".*/\1/p' windlab/disclaimer.py | head -n 1)"
PORT="${WINDLAB_PORT:-8080}"

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\n*** %s\n' "$*" >&2; exit 1; }

need_docker() {
    command -v docker >/dev/null 2>&1 || fail "docker was not found. Install Docker Desktop (macOS) or Docker Engine (Linux) first: see docs/INSTALL.md."
    docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker Desktop (macOS) or the docker service (Linux) and try again."
    docker compose version >/dev/null 2>&1 || fail "'docker compose' is not available. Update Docker Desktop, or install the docker-compose-plugin package on Linux."
}

# The educational-use notice, once per notice version (the page asks again in the browser; this is the
# command-line half, the same as  windlab start  on Linux).
accept_disclaimer() {
    local answer
    case "${WINDLAB_ACCEPT_DISCLAIMER:-}" in 1|true|yes|TRUE|YES) return 0 ;; esac
    if [ "${1:-}" = "--accept-disclaimer" ]; then
        mkdir -p "$(dirname "$ACCEPT_FILE")" && printf '%s\n' "$VERSION" > "$ACCEPT_FILE" || true
        return 0
    fi
    if [ -f "$ACCEPT_FILE" ] && [ "$(head -n 1 "$ACCEPT_FILE" 2>/dev/null | tr -d '[:space:]')" = "$VERSION" ]; then
        return 0
    fi
    printf '\n'
    cat DISCLAIMER.md
    printf '\n'
    if [ ! -t 0 ]; then
        fail "No terminal to ask on. Run again with  --accept-disclaimer  or set  WINDLAB_ACCEPT_DISCLAIMER=1  to accept the notice above."
    fi
    printf 'Type yes to accept and continue (anything else stops): '
    read -r answer || answer=""
    case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
        yes) ;;
        *) fail "Not accepted; nothing was started." ;;
    esac
    if mkdir -p "$(dirname "$ACCEPT_FILE")" && printf '%s\n' "$VERSION" > "$ACCEPT_FILE"; then
        printf 'Thank you. Remembered in %s; you will be asked again only if the notice changes.\n' "$ACCEPT_FILE"
    fi
}

cmd="${1:-up}"
case "$cmd" in
    shell)
        need_docker
        exec "${COMPOSE[@]}" exec windlab bash -l ;;
    doctor)
        need_docker
        exec "${COMPOSE[@]}" exec windlab windlab doctor ;;
    down)
        need_docker
        exec "${COMPOSE[@]}" down ;;
    up|--build|--accept-disclaimer)
        need_docker
        accept_disclaimer "${2:-$cmd}"
        if [ "$cmd" = "--build" ] || ! docker image inspect windlab:2606 >/dev/null 2>&1; then
            say "Building the WindLab image (Ubuntu 24.04 + OpenFOAM v2606; 15-25 minutes and about 3 GB the first time)"
            "${COMPOSE[@]}" build windlab
        fi
        say "Starting WindLab on http://localhost:$PORT/  (Ctrl+C stops it; runs are kept in the Docker volume 'runs')"
        export WINDLAB_ACCEPT_DISCLAIMER=1   # you said yes above (or earlier); the container's  windlab start  needs it
        exec "${COMPOSE[@]}" up windlab ;;
    *)
        printf 'usage: scripts/run_docker.sh [up|--build|shell|doctor|down|--accept-disclaimer]\n'
        exit 2 ;;
esac
