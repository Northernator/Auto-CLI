"""Tests for live-session driving (drive command)."""

import json

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
