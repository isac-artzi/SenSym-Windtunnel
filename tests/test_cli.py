"""windlab start: arguments, defaults, and that it hands the app to the server."""

from __future__ import annotations

import pytest

from windlab import cli


def run_start(argv, tmp_path, monkeypatch):
    monkeypatch.setenv("WINDLAB_DATA", str(tmp_path / "data"))
    args = cli.build_parser().parse_args(["start", *argv])
    seen = {}
    rc = cli.start(args, serve=lambda app, host, port: seen.update(app=app, host=host, port=port))
    return rc, seen


def test_defaults_bind_to_loopback(tmp_path, monkeypatch, capsys):
    rc, seen = run_start([], tmp_path, monkeypatch)
    assert rc == 0 and seen["host"] == "127.0.0.1" and seen["port"] == 8080
    out = capsys.readouterr().out
    assert "http://localhost:8080/" in out and "not yet validated" in out
    assert (tmp_path / "data").is_dir()


def test_container_host_flag(tmp_path, monkeypatch, capsys):
    rc, seen = run_start(["--host", "0.0.0.0", "--port", "8080"], tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert seen["host"] == "0.0.0.0" and "Docker" in out and "http://localhost:8080/" in out and "NOTE" in out


def test_bad_port(tmp_path, monkeypatch):
    rc, seen = run_start(["--port", "70000"], tmp_path, monkeypatch)
    assert rc == 2 and not seen


def test_housekeeping_options(tmp_path, monkeypatch, capsys):
    rc, seen = run_start(["--keep-runs", "5", "--disk-cap-gb", "2.5"], tmp_path, monkeypatch)
    s = seen["app"].fastapi.state.settings
    assert rc == 0 and s.keep_runs == 5 and s.disk_cap_bytes == int(2.5 * 2**30)
    assert "last 5 finished runs" in capsys.readouterr().out
    rc, seen = run_start(["--keep-runs", "0"], tmp_path, monkeypatch)
    assert rc == 2 and not seen


def test_default_cores_and_override(tmp_path, monkeypatch, capsys):
    rc, seen = run_start([], tmp_path, monkeypatch)
    s = seen["app"].fastapi.state.settings
    assert rc == 0 and s.n_procs == cli.default_procs() and "--procs N to change" in capsys.readouterr().out
    rc, seen = run_start(["--procs", "2"], tmp_path, monkeypatch)
    assert rc == 0 and seen["app"].fastapi.state.settings.n_procs == 2
    assert "cores per run:   2\n" in capsys.readouterr().out


def test_data_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDLAB_DATA", str(tmp_path / "x"))
    assert cli.default_data_dir() == tmp_path / "x"


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "WindLab" in capsys.readouterr().out
