# Run WindLab in Docker

This folder holds the Docker image WindLab runs in when it is not installed natively: the
same Ubuntu 24.04 and OpenFOAM v2606 that students get on WSL2, built for Apple Silicon
(arm64) and Intel/AMD (amd64) alike. It is the install route for **macOS** and for Linux
distributions other than Ubuntu, and a ready-made development environment for everyone.

| File | What it is |
|---|---|
| `Dockerfile` | Ubuntu 24.04 + OpenFOAM v2606 + the Python tools (numpy, FastAPI, cadquery-ocp for STEP, pytest, ruff). WindLab's own code is not copied in: the repository is mounted at `/work` at run time, so a `git pull` is live at once. |
| `compose.yaml` | Two services on that image: `windlab` (the app on http://localhost:8080) and `dev` (a shell, profile `dev`). Simulation output goes to the `runs` volume. |
| `smoke_test.sh` | Runs the stock OpenFOAM motorBike tutorial inside the container (about 354k cells) and prints the time: a quick check that OpenFOAM and MPI work. |

## Users: start WindLab

```bash
git clone https://github.com/isac-artzi/SenSym-Windtunnel.git
cd SenSym-Windtunnel
scripts/run_docker.sh          # shows DISCLAIMER.md and asks for "yes", builds the image (15-25 min once), starts WindLab
```

Then open **http://localhost:8080/**. `Ctrl+C` stops it. Other commands:
`scripts/run_docker.sh doctor` (the readiness check), `scripts/run_docker.sh shell` (a Linux
shell with OpenFOAM loaded), `scripts/run_docker.sh --build` (rebuild the image),
`scripts/run_docker.sh down` (remove the container; the runs volume stays). Set `WINDLAB_PORT=8081`
if 8080 is taken. The full guide, with Docker Desktop settings (give it at least 8 GB of memory)
and troubleshooting, is [`docs/INSTALL.md`](../docs/INSTALL.md).

Without the script: `WINDLAB_ACCEPT_DISCLAIMER=1 docker compose -f dev/compose.yaml up --build windlab`
(setting the variable means you accept `DISCLAIMER.md`).

## Developers: a shell in the container

```bash
docker compose -f dev/compose.yaml --profile dev up -d --build dev
docker compose -f dev/compose.yaml exec dev bash -l      # OpenFOAM loaded, repository at /work
pytest -q                                               # inside: the fast tests
windlab start --host 0.0.0.0                            # inside: the app on http://localhost:8090 outside
docker compose -f dev/compose.yaml exec -T dev bash /work/dev/smoke_test.sh   # the OpenFOAM smoke test
```

The `dev` service publishes port 8080 of the container as **8090** on your computer (so it does
not clash with a WindLab started the other way); `WINDLAB_DEV_PORT` changes that. Edit files on
your computer as usual; they appear live at `/work`. VS Code's "Reopen in Container" works with
`.devcontainer/devcontainer.json`.

Notes:

- The motorBike smoke test takes about 85 s on an Apple M-series desktop with 6 MPI ranks;
  expect several times longer on a laptop. Set WindLab's presets from laptop timings
  (`windlab benchmark`), never from a fast desktop.
- Docker Desktop gives its Linux VM a limited share of memory (Settings → Resources). A Quick
  run needs about 1 GB, "More accurate, slower" about 2 GB, Detailed about 7 GB; give it 8 GB
  or more before validation sweeps.
- Building the image with `CLAUDE_CODE=1` adds an AI coding assistant for maintainers; it is off
  by default and not needed to run or develop WindLab.
