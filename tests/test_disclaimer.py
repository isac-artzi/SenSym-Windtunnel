"""The educational-use notice (windlab/disclaimer.py): DISCLAIMER.md and the module agree, /api/config carries the
version, a run request without the acceptance header is refused, the command line asks for 'yes' once and remembers
it, and every report, CSV and export carries the one-line footer."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from tests.asgi_client import make_client
from tests.test_app import FakeSolver, naca
from windlab import cli, disclaimer
from windlab.app import Settings, create_app

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- the texts


def test_disclaimer_md_matches_the_module_and_says_what_the_owner_asked():
    text = (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8")
    assert text == disclaimer.FULL_TEXT
    assert disclaimer.RESPONSIBILITY == ("SenSym LLC assumes no responsibility for any damages or injury caused by "
                                         "objects built using this simulator.")
    for must in (disclaimer.RESPONSIBILITY, "EDUCATIONAL", "approximate and may be inaccurate", "not been validated",
                 "engineering design", "certification", "safety-critical", "physical testing", "qualified engineer",
                 "MIT License", "WITHOUT WARRANTY OF ANY KIND", '"AS IS"', "OPENFOAM®", disclaimer.FOOTER,
                 f"notice version {disclaimer.VERSION}", "Copyright (c) 2026 SenSym LLC"):
        assert must in text.replace("educational simulation", "EDUCATIONAL simulation"), must
    assert disclaimer.ACK_LABEL == ("I understand this is an educational simulation that may give inaccurate results, "
                                    "and SenSym LLC is not responsible for anything built with it")
    assert disclaimer.FOOTER == "Educational simulation — results may be inaccurate. See DISCLAIMER.md."
    assert disclaimer.FOOTER_ASCII.isascii() and "inaccurate" in disclaimer.FOOTER_ASCII
    for must in ("EDUCATIONAL", "MAY BE INACCURATE", disclaimer.RESPONSIBILITY, "AS IS", "DISCLAIMER.md"):
        assert must in disclaimer.CLI_TEXT, must


def test_license_and_readme():
    lic = (ROOT / "LICENSE").read_text()
    assert lic.startswith("MIT License\n\nCopyright (c) 2026 SenSym LLC\n") and 'PROVIDED "AS IS"' in lic
    readme = (ROOT / "README.md").read_text()
    for must in ("DISCLAIMER.md", "MIT", disclaimer.RESPONSIBILITY, "docs/INSTALL.md", "CONTRIBUTING.md",
                 "github.com/isac-artzi/SenSym-Windtunnel", "isac-artzi.github.io/SenSym-Windtunnel",
                 "OpenCFD Limited"):
        assert must in readme, must
    assert "CLAUDE.md" not in readme and "PRD" not in readme and "INSTALL_STUDENT" not in readme


# --------------------------------------------------------------------------- the server


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    yield a
    a.fastapi.state.runs.stop()


def test_config_carries_the_notice_and_the_full_text_is_served(env):
    c = make_client(env)
    d = c.get("/api/config").json()["disclaimer"]
    assert d["version"] == disclaimer.VERSION and d["header"] == disclaimer.HEADER
    assert d["ack"] == disclaimer.ACK_LABEL and disclaimer.RESPONSIBILITY in d["short"]
    r = c.get("/disclaimer")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.text == disclaimer.FULL_TEXT
    assert "x-content-type-options" in r.headers  # the usual security headers stay


def test_run_without_the_acceptance_header_is_refused(env):
    c = make_client(env, accept_disclaimer=False)
    gid = naca(c).json()["geometry_id"]
    body = {"geometry_id": gid, "airspeed": 20, "alpha_deg": 5}
    r = c.post("/api/runs", json=body)
    assert r.status_code == 403 and r.json()["detail"] == disclaimer.REJECTED
    assert "accept the notice" in r.json()["detail"]
    # a stale version is refused too; the current one is accepted
    r = c.post("/api/runs", json=body, headers={disclaimer.HEADER: "2000-01-01"})
    assert r.status_code == 403
    r = c.post("/api/runs", json=body, headers={disclaimer.HEADER: disclaimer.VERSION})
    assert r.status_code == 202, r.text
    assert env.fastapi.state.runs.wait_idle(20)
    assert c.get("/api/runs").json()["runs"][0]["status"] == "done"
    # the sweep route is the same route
    r = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "kind": "sweep", "angles": [0, 4]})
    assert r.status_code == 403


def test_reports_csv_and_exports_carry_the_footer(env):
    c = make_client(env)
    gid = naca(c).json()["geometry_id"]
    ids = [c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": a}).json()["run_id"]
           for a in (4, 8)]
    assert env.fastapi.state.runs.wait_idle(30)
    rid = ids[0]
    md = c.get(f"/api/runs/{rid}/report.md").text
    assert md.rstrip().endswith(f"*{disclaimer.FOOTER}*")
    page = c.get(f"/api/runs/{rid}/report.html").text
    assert f'<footer class="disclaimer"><p>{disclaimer.FOOTER}</p></footer>' in page
    for text in (c.get(f"/api/runs/{rid}/results.csv").text, c.get(f"/api/runs/{rid}/history.csv").text,
                 c.get("/api/compare.csv", params={"runs": ",".join(ids)}).text):
        assert f"# {disclaimer.FOOTER_ASCII}\n" in text
        text.encode("ascii")
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/runs/{rid}/case.zip").content))
    readme = next(n for n in z.namelist() if n.endswith("README.md"))
    assert z.read(readme).decode().rstrip().endswith(disclaimer.FOOTER)


# --------------------------------------------------------------------------- the command line


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fresh HOME with no ~/.windlab/accepted, and the environment variable the other tests set removed."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv(disclaimer.ENV_ACCEPT, raising=False)
    monkeypatch.setenv("WINDLAB_DATA", str(tmp_path / "data"))
    return tmp_path / "home"


def test_cli_accepts_through_environment_flag_or_file(home, monkeypatch):
    out = []
    assert disclaimer.accepted_file() == home / ".windlab" / "accepted"
    assert disclaimer.accepted_version() is None
    # environment: accepted, nothing written
    monkeypatch.setenv(disclaimer.ENV_ACCEPT, "1")
    assert disclaimer.require_cli_acceptance(out=out.append) and not out and not disclaimer.accepted_file().exists()
    monkeypatch.delenv(disclaimer.ENV_ACCEPT)
    # the flag: accepted and remembered
    assert disclaimer.require_cli_acceptance(True, out=out.append) and not out
    assert disclaimer.accepted_version() == disclaimer.VERSION
    # the file: accepted silently next time
    assert disclaimer.require_cli_acceptance(out=out.append) and not out
    # an older accepted version asks again
    disclaimer.accepted_file().write_text("1999-01-01\n")
    assert not disclaimer.require_cli_acceptance(out=out.append, ask=lambda _: "no")
    assert any("EDUCATIONAL" in x for x in out) and any("Not accepted" in x for x in out)
    assert disclaimer.accepted_version() == "1999-01-01"


def test_cli_asks_for_yes_and_remembers(home):
    out, asked = [], []

    def ask(prompt):
        asked.append(prompt)
        return " YES "

    assert disclaimer.require_cli_acceptance(out=out.append, ask=ask)
    assert len(asked) == 1 and "yes" in asked[0] and any(disclaimer.RESPONSIBILITY in x for x in out)
    assert disclaimer.accepted_version() == disclaimer.VERSION and any("Remembered" in x for x in out)
    # no terminal, no flag: refused with the two ways to accept
    disclaimer.accepted_file().unlink()
    out.clear()
    assert not disclaimer.require_cli_acceptance(out=out.append, interactive=False)
    assert any("--accept-disclaimer" in x and disclaimer.ENV_ACCEPT in x for x in out)
    # Ctrl+C or end of input at the prompt: refused
    def interrupted(_):
        raise EOFError

    assert not disclaimer.require_cli_acceptance(out=out.append, ask=interrupted)


def test_windlab_start_and_benchmark_are_gated(home, monkeypatch, capsys):
    seen = {}
    args = cli.build_parser().parse_args(["start"])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # no terminal
    assert cli.start(args, serve=lambda app, host, port: seen.update(host=host)) == cli.DISCLAIMER_NOT_ACCEPTED
    assert not seen and "EDUCATIONAL" in capsys.readouterr().out
    args = cli.build_parser().parse_args(["start", "--accept-disclaimer"])
    assert cli.start(args, serve=lambda app, host, port: seen.update(host=host)) == 0
    assert seen["host"] == "127.0.0.1" and disclaimer.accepted_version() == disclaimer.VERSION
    out = capsys.readouterr().out
    assert disclaimer.FOOTER_ASCII in out
    # remembered: start again without the flag
    seen.clear()
    args = cli.build_parser().parse_args(["start"])
    assert cli.start(args, serve=lambda app, host, port: seen.update(host=host)) == 0
    assert seen
    # benchmark checks its arguments first, then the notice
    disclaimer.accepted_file().unlink()
    assert cli.main(["benchmark", "--np", "500", "--data", str(home / "b")]) == 2
    rc = cli.benchmark(cli.build_parser().parse_args(["benchmark", "--np", "2", "--data", str(home / "b")]),
                       runner=lambda *a, **k: pytest.fail("the benchmark must not run before the notice is accepted"))
    assert rc == cli.DISCLAIMER_NOT_ACCEPTED
    # --help documents the flag and the variable
    with pytest.raises(SystemExit):
        cli.main(["start", "--help"])
    assert "--accept-disclaimer" in capsys.readouterr().out
