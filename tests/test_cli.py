"""Command regressions without requiring a live desktop or REA install."""

import subprocess
import json
import sys
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


def test_probe_file_target_saves_serializable_bundle(tmp_path, monkeypatch):
    target = tmp_path / "app.exe"
    target.touch()
    monkeypatch.setattr(rea_client, "find_rea", lambda: object())
    monkeypatch.setattr(rea_client, "run_probe", lambda *a, **k: rea_client.ProbeResult("analyze", [], True, envelope={}))
    out = tmp_path / "out"
    assert cli.main(["probe", "--target", str(target), "--out", str(out)]) == 0
    bundle = evidence.EvidenceBundle.load(out / "evidence-bundle.json")
    assert bundle.target == str(target.resolve())


def test_rea_version_rejects_nonzero_error_text(monkeypatch):
    monkeypatch.setattr(rea_client.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "npm failed"))
    with pytest.raises(subprocess.CalledProcessError):
        rea_client.version(rea_client.ReaLocation(["rea"], "test", "test"))


def test_probe_retains_process_failure_after_json_evidence(monkeypatch):
    import json

    envelope = {"normalized_result": {"integrity_contradictions": []}}
    diagnostic = "Failed to close the JavaScript analysis worker"
    monkeypatch.setattr(
        rea_client.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, json.dumps(envelope), diagnostic),
    )
    result = rea_client.run_probe(rea_client.ReaLocation(["rea"], "test", "test"), "analyze-javascript-application", ["app"])
    assert not result.ok and result.envelope is None
    assert result.error == envelope
    assert result.to_dict()["returncode"] == 1
    assert result.to_dict()["stderr"] == diagnostic


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


@pytest.mark.parametrize("options", [
    ["--agent-prepare-only"], ["--agent-task", "export library"],
    ["--agent-timeout", "1"], ["--agent", "codex", "--agent-timeout", "0"],
])
def test_auto_agent_options_checked_before_recon(options, monkeypatch, capsys):
    def unexpected():
        raise AssertionError("invalid options must fail before REA discovery")
    monkeypatch.setattr(rea_client, "find_rea", unexpected)
    assert cli.main(["auto", "--target", "anything", *options]) == 2
    assert "agent" in capsys.readouterr().err


def test_auto_prepares_verified_agent_handoff(tmp_path, monkeypatch):
    import json
    import sqlite3

    target = tmp_path / "library.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
    monkeypatch.setattr(rea_client, "find_rea", lambda: object())
    monkeypatch.setattr(rea_client, "run_probe", lambda *a, **k: rea_client.ProbeResult("analyze", [], True, envelope={}))
    monkeypatch.setattr(cli.vmod, "validate", lambda root: SimpleNamespace(ok=True, lines=lambda: []))
    monkeypatch.setattr(cli.deep, "_agent_command", lambda agent: pytest.fail("prepare must not invoke an agent"))
    out = tmp_path / "out"
    assert cli.main([
        "auto", "--target", str(target), "--name", "library", "--out", str(out),
        "--agent", "claude", "--agent-prepare-only", "--agent-task", "Export a reading list",
    ]) == 0
    run_dir = next((out / "library" / "agent-harness" / "deep-workflow").glob("run-*"))
    assert json.loads((run_dir / "report.json").read_text())["status"] == "prepared"
    assert "Export a reading list" in (run_dir / "prompt.md").read_text()
    verification = json.loads((run_dir / "verification.json").read_text())
    assert any(c["state"] == "verified" for c in verification["checks"])


def test_deep_reports_input_error_without_traceback(tmp_path, capsys):
    bundle = tmp_path / "bundle.json"
    evidence.EvidenceBundle(target="missing").save(bundle)
    assert cli.main([
        "deep", "--bundle", str(bundle), "--dir", str(tmp_path),
        "--agent", "codex", "--prepare-only",
    ]) == 2
    assert "no generated harness" in capsys.readouterr().err


def test_auto_launch_keeps_gui_alive_through_live_verification_and_deep(tmp_path, monkeypatch):
    target = tmp_path / "wordpad.exe"
    target.write_bytes(b"binary fixture")
    events = []
    proc = SimpleNamespace(pid=42, terminate=lambda: events.append("terminated"))
    def launch(argv, **kwargs):
        assert argv == [str(target.resolve())]
        assert "shell" not in kwargs
        events.append("launched")
        return proc
    monkeypatch.setattr(cli.subprocess, "Popen", launch)
    monkeypatch.setattr(rea_client, "find_rea", lambda: object())
    monkeypatch.setattr(rea_client, "run_probe", lambda *a, **k: rea_client.ProbeResult("analyze", [], False))
    window = SimpleNamespace(ClassName="WordPadClass", ProcessId=42, NativeWindowHandle=123, Name="fixture.rtf")
    def find_window(**selectors):
        assert selectors == {"pid": 42, "process": "WordPadClass", "title_contains": None, "executable": str(target.resolve())}
        assert "terminated" not in events
        return window
    monkeypatch.setattr(gui, "find_window", find_window)
    monkeypatch.setattr(gui, "window_executable", lambda pid: str(target.resolve()))
    monkeypatch.setattr(gui, "capture_tree", lambda window: [gui.UiNode("fixture.rtf > Rich Text Window", "DocumentControl", "Rich Text Window", ["Text"], 1)])
    monkeypatch.setattr(gui, "_find_node", lambda window, path: object())
    monkeypatch.setattr(gui, "read_text", lambda node: "fixture content")
    monkeypatch.setattr(gui, "upgrade_scaffold", lambda *a: True)
    document = SimpleNamespace(upgrade_scaffold=lambda *a, **k: events.append("document installed") or True)
    import auto_cli
    monkeypatch.setitem(sys.modules, "auto_cli.document", document)
    monkeypatch.setattr(auto_cli, "document", document, raising=False)
    def validate(root):
        assert "terminated" not in events
        verification = json.loads((root / "verification.json").read_text())
        assert cli.vfy.verified_gui_surface(verification, str(target.resolve()))
        return SimpleNamespace(ok=True, lines=lambda: [])
    monkeypatch.setattr(cli.vmod, "validate", validate)
    monkeypatch.setattr(cli.deep, "_agent_command", lambda agent: pytest.fail("prepare-only must not invoke an agent"))
    out = tmp_path / "out"
    assert cli.main([
        "auto", "--target", str(target), "--out", str(out), "--name", "wordpad", "--launch",
        "--gui-process", "WordPadClass", "--agent", "codex", "--agent-prepare-only",
    ]) == 0
    assert events == ["launched", "document installed", "terminated"]
    root = out / "wordpad" / "agent-harness"
    handoff = next((root / "deep-workflow").glob("run-*"))
    assert json.loads((handoff / "verification.json").read_text())["counts"] == {"verified": 3}
    assert json.loads((root / "gui.json").read_text())["executable"] == str(target.resolve())


def test_verify_missing_requested_gui_is_failed_not_silent_success(tmp_path, monkeypatch):
    target = tmp_path / "wordpad.exe"
    target.write_bytes(b"binary fixture")
    bundle = evidence.EvidenceBundle(target=str(target))
    bundle_file = tmp_path / "bundle.json"
    bundle.save(bundle_file)
    monkeypatch.setattr(gui, "find_window", lambda **kwargs: None)
    assert cli.main([
        "verify", "--bundle", str(bundle_file), "--dir", str(tmp_path), "--gui-process", "WordPadClass",
    ]) == 2
    checks = json.loads((tmp_path / "verification.json").read_text())["checks"]
    assert checks[0]["kind"] == "gui_window" and checks[0]["state"] == "failed"


def test_auto_direct_launch_parser_preserves_explicit_commands():
    parser = cli.build_parser()
    assert parser.parse_args(["auto", "--target", "app.exe", "--launch"]).launch is True
    assert parser.parse_args(["auto", "--target", "app.js", "--launch", "node --inspect=9229 app.js"]).launch == "node --inspect=9229 app.js"


@pytest.mark.parametrize("command", ["scaffold", "pipeline", "auto"])
@pytest.mark.parametrize("skin_kind", ["missing", "directory"])
def test_explicit_missing_repl_skin_fails_before_recon_or_output(tmp_path, monkeypatch, capsys, command, skin_kind):
    skin = tmp_path / "repl_skin.py"
    if skin_kind == "directory":
        skin.mkdir()
    out = tmp_path / "out"
    def unexpected(*args, **kwargs):
        pytest.fail("invalid explicit REPL skin must fail before recon or bundle loading")
    monkeypatch.setattr(rea_client, "find_rea", unexpected)
    monkeypatch.setattr(cli, "_load_bundle", unexpected)
    target_args = ["--bundle", "unused.json"] if command == "scaffold" else ["--target", "unused.exe"]
    assert cli.main([command, *target_args, "--repl-skin", str(skin), "--out", str(out)]) == 2
    assert "REPL skin file not found" in capsys.readouterr().err
    assert not out.exists()
