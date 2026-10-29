import runpy

import pytest

from pdd_agent import cli


def test_invalid_config_exits_with_all_errors(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert "cannot read" in capsys.readouterr().err
    assert cli.main([]) == 2
    assert "pdd.jwtSecret: required" in capsys.readouterr().err


def test_serves_with_configured_address(tmp_path, monkeypatch):
    config = tmp_path / "v.yaml"
    config.write_text(
        "pdd: {jwtSecret: s}\ngitGateway: {internalToken: t}\nllm: {apiKey: k}\n"
        "sandbox: {internalToken: st}\n"
        "server: {listenAddr: '127.0.0.1:9123', logLevel: warn}\n"
        f"storage: {{dataDir: {tmp_path}}}\n"
    )
    calls = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.update(kw))
    assert cli.main(["--config", str(config)]) == 0
    assert (calls["host"], calls["port"]) == ("127.0.0.1", 9123)


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_module_entry_point(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pdd-agent", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("pdd_agent", run_name="__main__")
    assert exit_info.value.code == 0
