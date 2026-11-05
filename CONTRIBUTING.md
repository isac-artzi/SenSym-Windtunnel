# Contributing to WindLab

Thank you for helping. WindLab is a small open-source project (MIT License) for engineering
students; please keep that audience in mind: plain language in the page and the docs, no new
dependencies without a good reason, nothing that sends data anywhere.

## Reporting a problem

Open a GitHub issue with the **Bug report** template. Say which operating system and install
route you used (Windows/WSL2, macOS/Docker or Linux), what you did, what you expected, what
happened, and paste the output of `windlab doctor`. Screenshots of the page help. Do not
attach a part you are not allowed to share; a built-in shape that shows the same problem is
better anyway.

## Suggesting a change

Open an issue first for anything bigger than a typo, so we can agree on the approach. Pull
requests are welcome for:

- fixes and clearer wording (the page's text lives in `windlab/app/static/i18n/en.json`);
- new lessons (`windlab/lessons/`, see `docs/lessons/README.md`);
- validation evidence (`validation/`): a new reference case with its source;
- install and troubleshooting notes for another operating system or network.

## Working on the code

```bash
git clone https://github.com/isac-artzi/SenSym-Windtunnel.git
cd SenSym-Windtunnel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,step]"
ruff check .          # lint
pytest -q             # fast tests with a fake solver (needs Node for the page tests)
pytest -m slow        # the real OpenFOAM run (needs OpenFOAM v2606 installed)
```

Without OpenFOAM on your machine, use the Docker image: `scripts/run_docker.sh shell` opens a
shell with everything installed (`dev/README.md`).

Conventions: Python 3.12, type hints, `ruff` clean, tests next to the feature in `tests/`.
Case files are generated from the templates in `windlab/templates/` with clamped numbers; never
add a way for user text to reach an OpenFOAM dictionary or a shell. The server binds to
127.0.0.1 only. Every result stays labelled "uncalibrated prototype — not yet validated" and the
educational-use notice (`DISCLAIMER.md`) stays in place.

By contributing you agree that your contribution is licensed under the MIT License of this
repository. OPENFOAM® is a registered trade mark of OpenCFD Limited; keep the trade-mark
notice as it is and never put "OpenFOAM" in the product's name.
