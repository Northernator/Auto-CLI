"""Command regressions without requiring a live desktop or REA install."""

import subprocess
from types import SimpleNamespace

import pytest

from auto_cli import cli, evidence, gui, rea_client


@pytest.mark.parametrize("suffix", [".db", ".sqlite", ".sqlite3"])
def test_direct_database_target(tmp_path, suffix):
    target = tmp_path / ("library" + suffix)
    target.touch()
    probes = dict(cli._target_probes(target, None))
    assert probes["inspect-sqlite-database"] == [str(target), "--schema-scope", "database"]
    findings = evidence.local_filesystem_probe(target)
    assert [(f.kind, f.value) for f in findings] == [("database", str(target.resolve()))]


@pytest.mark.parametrize("suffix", [".js", ".mjs", ".cjs", ".asar"])
def test_direct_javascript_target(tmp_path, suffix):
    target = tmp_path / ("app" + suffix)
    target.touch()
    assert "analyze-javascript-application" in dict(cli._target_probes(target, None))


def test_rea_version_rejects_nonzero_error_text(monkeypatch):
    monkeypatch.setattr(rea_client.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "npm failed"))
    with pytest.raises(subprocess.CalledProcessError):
        rea_client.version(rea_client.ReaLocation(["rea"], "test", "test"))


def test_local_gui_skips_rea_discovery(monkeypatch):
    def unexpected():
        raise AssertionError("local backend must not run REA discovery")
    monkeypatch.setattr(rea_client, "find_rea", unexpected)
    monkeypatch.setattr(cli, "_cmd_gui_local", lambda args: 0)
    assert cli.main(["gui", "--backend", "local", "--window-id", "123"]) == 0


def test_local_gui_honors_window_handle_and_failed_status(tmp_path, monkeypatch):
    window = SimpleNamespace(ClassName="Test", ProcessId=123, Name="Test")
    handles = []
    def by_handle(handle):
        handles.append(handle)
        return window
    monkeypatch.setattr(gui, "find_window_by_handle", by_handle)
    monkeypatch.setattr(gui, "capture_tree", lambda *a, **k: [])
    monkeypatch.setattr(gui, "act", lambda *a, **k: gui.GuiAction("invoke", "missing", detail="not found"))
    result = cli.main(["gui", "--backend", "local", "--window-id", "456", "--invoke", "missing", "--out", str(tmp_path)])
    assert result == 2
    assert handles == [456]
    assert (tmp_path / "GUI-REPORT.md").exists()


def test_forced_rea_backend_does_not_fallback(monkeypatch):
    def fail(*a, **k):
        raise gui.GuiError("provider failed")
    monkeypatch.setattr(gui, "capture_via_rea", fail)
    args = cli.build_parser().parse_args(["gui", "--backend", "rea"])
    assert cli._cmd_gui_via_rea(args, object(), 123, 456) == 1


def test_rea_gui_failed_action_status(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "capture_via_rea", lambda *a, **k: [])
    monkeypatch.setattr(gui, "act_via_rea", lambda *a, **k: gui.GuiAction("invoke", "missing", detail="not found"))
    args = cli.build_parser().parse_args(["gui", "--backend", "rea", "--invoke", "missing", "--out", str(tmp_path)])
    assert cli._cmd_gui_via_rea(args, object(), 123, 456) == 2


def test_auto_missing_requested_gui_fails(tmp_path, monkeypatch, capsys):
    target = tmp_path / "app.exe"
    target.touch()
    monkeypatch.setattr(rea_client, "find_rea", lambda: object())
    monkeypatch.setattr(rea_client, "run_probe", lambda *a, **k: rea_client.ProbeResult("analyze", [], False))
    monkeypatch.setattr(gui, "find_window", lambda **k: None)
    monkeypatch.setattr(cli.vmod, "validate", lambda root: SimpleNamespace(ok=True, lines=lambda: []))
    assert cli.main(["auto", "--target", str(target), "--name", "test", "--out", str(tmp_path / "out"), "--gui-title", "Missing"]) == 2
    assert "result: FAILED" in capsys.readouterr().out
