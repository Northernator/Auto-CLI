"""Verified coding-agent handoffs without invoking an authenticated agent."""

import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from auto_cli import deep, evidence, scaffold, surface, validate


@pytest.fixture()
def built(tmp_path):
    bundle = evidence.EvidenceBundle(
        target=str(tmp_path / "target"),
        findings=[
            evidence.Finding(
                kind="endpoint", value="http://127.0.0.1:18110/api/tracks",
                source_command="analyze", source_file="main.js", line=12,
                artifact_sha256="evidence-digest",
            ),
            evidence.Finding(kind="ipc_channel", value="library:load", source_command="analyze"),
        ],
        limitations=["IPC requires a running session"],
    )
    root = scaffold.generate(surface.classify(bundle), bundle, tmp_path / "out", name="deep-app")
    verification = {
        "target": bundle.target,
        "counts": {"verified": 1, "unverifiable": 1},
        "checks": [
            {"kind": "endpoint", "target": bundle.findings[0].value, "state": "verified", "detail": "200 JSON"},
            {"kind": "ipc_channel", "target": "library:load", "state": "unverifiable", "detail": "static only"},
        ],
        "patches": {},
    }
    (root / "verification.json").write_text(json.dumps(verification), encoding="utf-8")
    return bundle, root, verification


def _validation(root, ok=True):
    return validate.ValidationReport(root, [validate.Check("workflow tests", ok, "tests exercised")])


def _must_not_run(*args, **kwargs):
    pytest.fail("this operation must not run")


def test_prepare_snapshots_evidence_without_agent_or_validation(built, monkeypatch):
    bundle, root, verification = built
    (root / "drive.json").write_text('{"action": "observed"}', encoding="utf-8")
    (root / "gui.json").write_text('{"window": "observed"}', encoding="utf-8")
    monkeypatch.setattr(deep, "_agent_command", _must_not_run)
    monkeypatch.setattr(deep, "_run_agent", _must_not_run)
    monkeypatch.setattr(deep.vmod, "validate", _must_not_run)

    task = 'Compose a "reading list" export'
    report = deep.generate(bundle, root, agent="claude", task=task, prepare_only=True)

    assert report.ok and report.status == "prepared"
    assert report.returncode is None and report.error == ""
    assert report.run_dir.parent == root / "deep-workflow"
    assert json.loads((report.run_dir / "evidence-bundle.json").read_text()) == bundle.to_dict()
    assert json.loads((report.run_dir / "verification.json").read_text()) == verification
    assert json.loads((report.run_dir / "plan.json").read_text())["target"] == bundle.target
    assert json.loads((report.run_dir / "drive.json").read_text()) == {"action": "observed"}
    assert json.loads((report.run_dir / "gui.json").read_text()) == {"window": "observed"}
    prompt = (report.run_dir / "prompt.md").read_text()
    assert json.dumps(task) in prompt
    assert "verified/corrected" in prompt
    assert "Failed/unverifiable" in prompt
    assert "confirmation gates" in prompt and "backup-before-write" in prompt
    assert not (report.run_dir / "agent.stdout.log").exists()
    assert not (report.run_dir / "validation.json").exists()
    assert json.loads((report.run_dir / "report.json").read_text()) == report.to_dict()
    assert "no agent ran" in (root / "DEEP-REPORT.md").read_text()


def test_prepare_preserves_previous_runs(built, monkeypatch):
    bundle, root, _ = built
    monkeypatch.setattr(deep, "_agent_command", _must_not_run)
    first = deep.generate(bundle, root, agent="codex", task="First workflow", prepare_only=True)
    first_prompt = (first.run_dir / "prompt.md").read_text()
    second = deep.generate(bundle, root, agent="codex", task="Second workflow", prepare_only=True)
    assert first.run_dir != second.run_dir
    assert (first.run_dir / "prompt.md").read_text() == first_prompt
    assert "Second workflow" in (second.run_dir / "prompt.md").read_text()
    assert str(second.run_dir) in (root / "DEEP-REPORT.md").read_text()


def test_corrected_runtime_evidence_is_accepted(built):
    bundle, root, verification = built
    verification["checks"] = [{
        "kind": "endpoint", "target": bundle.findings[0].value, "state": "corrected",
        "verified_value": "http://127.0.0.1:18110/api/library/tracks",
        "detail": "corrected endpoint",
    }]
    (root / "verification.json").write_text(json.dumps(verification), encoding="utf-8")
    report = deep.generate(bundle, root.parent, agent="codex", prepare_only=True)
    assert report.ok
    assert json.loads((report.run_dir / "verification.json").read_text())["checks"] == verification["checks"]
    assert "Use verified_value for corrected findings" in (report.run_dir / "prompt.md").read_text()


@pytest.mark.parametrize("change", ["missing", "mismatched", "failed", "unverifiable", "empty", "malformed"])
def test_requires_runtime_verification_before_preparing(built, monkeypatch, change):
    bundle, root, verification = built
    path = root / "verification.json"
    if change == "missing":
        path.unlink()
    else:
        if change == "mismatched":
            verification["target"] = "another application"
        elif change in {"failed", "unverifiable"}:
            verification["checks"] = [{"target": bundle.findings[0].value, "state": change}]
        elif change == "empty":
            verification["checks"] = []
        else:
            verification["checks"] = [None, "verified", {"state": "verified"}]
        path.write_text(json.dumps(verification), encoding="utf-8")
    monkeypatch.setattr(deep, "_agent_command", _must_not_run)
    with pytest.raises(ValueError, match="verification|verified/corrected"):
        deep.generate(bundle, root, agent="codex", prepare_only=True)
    assert not (root / "deep-workflow").exists()


@pytest.mark.parametrize("options", [{"agent": "other"}, {"agent": "codex", "timeout": 0}, {"agent": "claude", "timeout": -1}])
def test_invalid_agent_options_rejected_before_any_handoff(built, options):
    bundle, root, _ = built
    with pytest.raises(ValueError):
        deep.generate(bundle, root, prepare_only=True, **options)
    assert not (root / "deep-workflow").exists()


def test_success_requires_validation_and_uses_stdin(built, monkeypatch):
    bundle, root, _ = built
    argv = ["fake-codex", "exec", "-"]
    monkeypatch.setattr(deep, "_agent_command", lambda agent: argv)
    calls = []

    def run(command, prompt, workspace, run_dir, timeout):
        calls.append(command)
        assert command == argv
        assert workspace == root.resolve()
        assert timeout == 23
        assert "Implement deeper" in prompt
        (run_dir / "agent.stdout.log").write_text("Implemented workflows\n", encoding="utf-8")
        (run_dir / "agent.stderr.log").write_text("agent diagnostic\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(deep, "_run_agent", run)
    validations = []

    def check(path):
        validations.append(path)
        return _validation(path)

    monkeypatch.setattr(deep.vmod, "validate", check)
    report = deep.generate(bundle, root, agent="codex", timeout=23)
    assert report.ok and report.status == "completed" and report.returncode == 0
    assert len(calls) == 1 and validations == [root]
    assert (report.run_dir / "agent.stdout.log").read_text() == "Implemented workflows\n"
    assert (report.run_dir / "agent.stderr.log").read_text() == "agent diagnostic\n"
    assert json.loads((report.run_dir / "validation.json").read_text())["ok"] is True
    assert json.loads((report.run_dir / "report.json").read_text())["status"] == "completed"


def test_nonzero_agent_preserves_logs_and_skips_validation(built, monkeypatch):
    bundle, root, _ = built
    monkeypatch.setattr(deep, "_agent_command", lambda agent: ["fake-claude"])
    monkeypatch.setattr(deep.vmod, "validate", _must_not_run)

    def run(command, prompt, workspace, run_dir, timeout):
        (run_dir / "agent.stderr.log").write_text("Authentication failed\n", encoding="utf-8")
        return 7

    monkeypatch.setattr(deep, "_run_agent", run)
    report = deep.generate(bundle, root, agent="claude")
    assert not report.ok and report.status == "failed" and report.returncode == 7
    assert "7" in report.error
    assert "Authentication failed" in (report.run_dir / "agent.stderr.log").read_text()
    assert not (report.run_dir / "validation.json").exists()
    assert json.loads((report.run_dir / "report.json").read_text())["status"] == "failed"


def test_agent_success_with_failed_validation_is_failed(built, monkeypatch):
    bundle, root, _ = built
    monkeypatch.setattr(deep, "_agent_command", lambda agent: ["fake-codex"])
    monkeypatch.setattr(deep, "_run_agent", lambda *args, **kwargs: 0)
    monkeypatch.setattr(deep.vmod, "validate", lambda path: _validation(path, ok=False))
    report = deep.generate(bundle, root, agent="codex")
    assert not report.ok and report.status == "failed" and report.returncode == 0
    assert "validation failed" in report.error
    validation_doc = json.loads((report.run_dir / "validation.json").read_text())
    assert validation_doc["ok"] is False
    assert validation_doc["checks"][0]["name"] == "workflow tests"


def test_agent_timeout_preserves_partial_output_and_report(built, monkeypatch):
    bundle, root, _ = built
    monkeypatch.setattr(deep, "_agent_command", lambda agent: ["fake-codex"])
    monkeypatch.setattr(deep.vmod, "validate", _must_not_run)

    def run(command, prompt, workspace, run_dir, timeout):
        (run_dir / "agent.stdout.log").write_text("partial implementation\n", encoding="utf-8")
        (run_dir / "agent.stderr.log").write_text("partial diagnostic\n", encoding="utf-8")
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(deep, "_run_agent", run)
    report = deep.generate(bundle, root, agent="codex", timeout=2)
    assert not report.ok and report.status == "timeout" and report.returncode is None
    assert "2s" in report.error
    assert "partial implementation" in (report.run_dir / "agent.stdout.log").read_text()
    assert "partial diagnostic" in (report.run_dir / "agent.stderr.log").read_text()
    assert json.loads((report.run_dir / "report.json").read_text())["status"] == "timeout"


@pytest.mark.parametrize("failure", ["missing", "launch"])
def test_agent_os_errors_are_recorded(built, monkeypatch, failure):
    bundle, root, _ = built
    monkeypatch.setattr(deep.vmod, "validate", _must_not_run)

    def fail(*args, **kwargs):
        raise FileNotFoundError("agent executable unavailable")

    monkeypatch.setattr(deep, "_agent_command", fail if failure == "missing" else lambda agent: ["fake-codex"])
    monkeypatch.setattr(deep, "_run_agent", fail)
    report = deep.generate(bundle, root, agent="codex")
    assert not report.ok and report.status == "failed" and report.returncode is None
    assert "executable unavailable" in report.error
    assert json.loads((report.run_dir / "report.json").read_text())["error"] == report.error


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_windows_agent_resolver_prefers_native_binary(tmp_path, monkeypatch, agent):
    native = str(tmp_path / (agent + ".exe"))
    lookups = []

    def which(name):
        lookups.append(name)
        return native if name == agent + ".exe" else str(tmp_path / (agent + ".cmd"))

    monkeypatch.setattr(deep, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(deep.shutil, "which", which)
    argv = deep._agent_command(agent)
    assert argv[0] == native and lookups == [agent + ".exe"]
    assert not any("dangerous" in arg or arg == "bypassPermissions" for arg in argv)
    if agent == "codex":
        assert argv[1:] == ["exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "-"]
    else:
        assert argv[1:] == ["--print", "--permission-mode", "acceptEdits", "--input-format", "text"]


@pytest.mark.parametrize("agent,entry", [
    ("codex", "@openai/codex/bin/codex.js"),
    ("claude", "@anthropic-ai/claude-code/cli.js"),
])
def test_windows_npm_agent_uses_node_without_shell(tmp_path, monkeypatch, agent, entry):
    shim = tmp_path / (agent + ".cmd")
    script = tmp_path / "node_modules" / entry
    script.parent.mkdir(parents=True)
    script.write_text("// npm entry point", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.touch()
    monkeypatch.setattr(deep, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(deep.shutil, "which", lambda name: str(shim) if name == agent else None)
    assert deep._agent_command(agent)[:2] == [str(node), str(script)]


def test_unresolved_npm_shim_is_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(deep, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(deep.shutil, "which", lambda name: str(tmp_path / "codex.cmd") if name == "codex" else None)
    with pytest.raises(FileNotFoundError, match="native CLI|npm entry point"):
        deep._agent_command("codex")


def test_missing_agent_is_an_actionable_error(monkeypatch):
    monkeypatch.setattr(deep.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="prepare-only"):
        deep._agent_command("claude")


def test_agent_runner_passes_utf8_prompt_by_stdin_and_records_logs(tmp_path):
    code = (
        "import os, sys; "
        "data = sys.stdin.buffer.read().replace(b'\\r\\n', b'\\n'); "
        "print(data.hex()); print(os.getcwd()); "
        "sys.stderr.write('agent diagnostic\\n')"
    )
    prompt = "Workflow priorities: café\nwith multiple lines"
    result = deep._run_agent([sys.executable, "-c", code], prompt, tmp_path, tmp_path, 10)
    assert result == 0
    stdout = (tmp_path / "agent.stdout.log").read_text()
    assert prompt.encode("utf-8").hex() in stdout
    assert str(tmp_path) in stdout
    assert "agent diagnostic" in (tmp_path / "agent.stderr.log").read_text()


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "keyboard-interrupt"])
def test_agent_stop_terminates_spawned_child_before_it_can_edit(tmp_path, monkeypatch, cancel):
    started = tmp_path / "child-started"
    edited = tmp_path / "child-edited"
    child = (
        "import pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text('started'); "
        "time.sleep(2); pathlib.Path(sys.argv[2]).write_text('late edit')"
    )
    parent = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]]); "
        "print('agent started', flush=True); time.sleep(30)"
    )
    argv = [sys.executable, "-c", parent, child, str(started), str(edited)]
    if cancel:
        communicate = subprocess.Popen.communicate
        interrupted = False

        def interrupt_agent(proc, *args, **kwargs):
            nonlocal interrupted
            if proc.args == argv and not interrupted:
                deadline = time.monotonic() + 5
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert started.exists(), "agent child did not start before cancellation"
                interrupted = True
                raise KeyboardInterrupt
            return communicate(proc, *args, **kwargs)

        monkeypatch.setattr(subprocess.Popen, "communicate", interrupt_agent)
    with pytest.raises(KeyboardInterrupt if cancel else subprocess.TimeoutExpired):
        deep._run_agent(argv, "", tmp_path, tmp_path, 10 if cancel else 1)
    assert started.exists(), "the child must actually start for the timeout check to be meaningful"
    time.sleep(2)
    assert not edited.exists(), "stopped agent descendants must not keep changing files"
    assert "agent started" in (tmp_path / "agent.stdout.log").read_text()
