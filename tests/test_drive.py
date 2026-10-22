"""Tests for live-session driving (drive command)."""

import json
import socket
import subprocess
import sys
import zipfile

import pytest

from auto_cli import drive as drv, evidence as ev, scaffold, surface


def _scaffold(tmp_path):
    findings = [
        ev.Finding(kind="ipc_channel", value="library:load", source_command="analyze", notes=["side=renderer op=on"]),
        ev.Finding(kind="ipc_channel", value="library:export", source_command="analyze", notes=["side=renderer op=on"]),
    ]
    bundle = ev.EvidenceBundle(target=str(tmp_path / "app"), findings=findings)
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="driven-app")
    return bundle, plan, root


def _report(live):
    r = drv.DriveReport(target="/app", inspector="http://127.0.0.1:9229")
    r.live_channels = live
    r.steps.append(drv.DriveStep(name="send x", ok=True, detail="responded"))
    return r


def test_upgrade_implements_send(tmp_path):
    bundle, plan, root = _scaffold(tmp_path)
    drv._upgrade_scaffold(root, plan, _report({"library:load": '{"ok":true,"tracks":1}'}))
    cli_src = (root / "cli_anything" / "driven_app" / "driven_app_cli.py").read_text()
    assert "LIVE_CHANNELS" in cli_src
    assert "inspector_send.mjs" in cli_src
    assert "not wired yet" not in cli_src


def test_upgrade_writes_transport_and_tests(tmp_path):
    bundle, plan, root = _scaffold(tmp_path)
    drv._upgrade_scaffold(root, plan, _report({"library:load": '{"ok":true}'}))
    pkg = root / "cli_anything" / "driven_app"
    assert (pkg / "utils" / "inspector_send.mjs").exists()
    backend = (pkg / "core" / "backend.py").read_text()
    assert "INSPECTOR_ENDPOINT = 'http://127.0.0.1:9229'" in backend
    assert "LIVE_CHANNELS = ['library:load']" in backend
    tests = (pkg / "tests" / "test_core.py").read_text()
    assert "test_send_rejects_unconfirmed_channel" in tests
    skill = (pkg / "skills" / "SKILL.md").read_text()
    assert "Driven live" in skill
    assert "library:load" in skill


def test_upgrade_noop_without_live_channels(tmp_path):
    bundle, plan, root = _scaffold(tmp_path)
    before = (root / "cli_anything" / "driven_app" / "driven_app_cli.py").read_text()
    drv._upgrade_scaffold(root, plan, _report({}))
    after = (root / "cli_anything" / "driven_app" / "driven_app_cli.py").read_text()
    assert before == after  # nothing confirmed live -> no upgrade


def test_find_target_id():
    doc = {"normalized_result": {"targets": [{"target_id": "abc-123-def"}]}}
    assert drv._find_target_id(doc) == "abc-123-def"


def test_inspector_send_mjs_is_valid_js(tmp_path):
    """The embedded transport must be syntactically loadable by node."""
    import subprocess

    p = tmp_path / "inspector_send.mjs"
    p.write_text(drv.INSPECTOR_SEND_MJS)
    proc = subprocess.run(
        ["node", "--check", str(p)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr


def test_report_markdown():
    r = _report({"library:load": '{"ok":true}'})
    r.limitations.append("REA never mutates")
    md = r.markdown()
    assert "Drive report" in md
    assert "library:load" in md
    assert "REA never mutates" in md


def test_write_report(tmp_path):
    bundle, plan, root = _scaffold(tmp_path)
    p = drv.write_report(_report({"library:load": "{}"}), root)
    assert p.name == "DRIVE-REPORT.md"
    assert (root / "drive.json").exists()


def test_null_response_is_not_confirmed_live(tmp_path, monkeypatch):
    bundle, plan, root = _scaffold(tmp_path)
    monkeypatch.setattr(drv, "_run_inspector_eval", lambda *_args: "null")
    report = drv.drive(bundle, plan, root, "http://127.0.0.1:9229")
    assert not report.live_channels
    assert all(not step.ok for step in report.steps if step.name.startswith("send "))
    assert "LIVE_CHANNELS" not in (root / "cli_anything/driven_app/core/backend.py").read_text()


def test_inspector_process_failure_is_reported(monkeypatch):
    monkeypatch.setattr(
        drv.subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "WebSocket is not defined"),
    )
    with pytest.raises(OSError, match="WebSocket is not defined"):
        drv._run_inspector_eval("http://127.0.0.1:9229", "1")


def test_async_channel_is_awaited_by_probe_and_transport(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = subprocess.Popen(
        ["node", f"--inspect=127.0.0.1:{port}", "-e",
         "globalThis.channels={send:async(c,p)=>{await new Promise(r=>setTimeout(r,10));return {channel:c,value:p.value||42}}};setInterval(()=>{},1000)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    endpoint = f"http://127.0.0.1:{port}"
    try:
        assert drv.wait_for_inspector(endpoint, timeout_s=5)
        expr = drv.SEND_PROBE_TEMPLATE.format(channel=json.dumps("library:load"), payload="{}")
        assert json.loads(drv._run_inspector_eval(endpoint, expr)) == {"channel": "library:load", "value": 42}
        script = tmp_path / "inspector_send.mjs"
        script.write_text(drv.INSPECTOR_SEND_MJS, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(script), endpoint, "library:load", '{"value": 7}'],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == {"channel": "library:load", "value": 7}
    finally:
        app.terminate()
        app.wait(timeout=10)


def test_generated_wheel_contains_live_transport(tmp_path):
    pytest.importorskip("wheel")
    bundle, plan, root = _scaffold(tmp_path)
    drv._upgrade_scaffold(root, plan, _report({"library:load": '{"ok":true}'}))
    dist = tmp_path / "dist"
    proc = subprocess.run(
        [sys.executable, "setup.py", "bdist_wheel", "--dist-dir", str(dist)],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with zipfile.ZipFile(next(dist.glob("*.whl"))) as wheel:
        assert "cli_anything/driven_app/utils/inspector_send.mjs" in wheel.namelist()
