"""Tests for scaffold generation: layout, registry entry, and the generated
harness's own test suite actually passing."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from auto_cli import evidence as ev
from auto_cli import scaffold, surface, validate as vmod


@pytest.fixture()
def plan_and_bundle():
    findings = [
        ev.Finding(kind="database", value="library.db", source_command="local-fs"),
        ev.Finding(kind="endpoint", value="http://127.0.0.1:18110/api/library/tracks", source_command="analyze"),
        ev.Finding(kind="ipc_channel", value="library:load", source_command="analyze", notes=["side=renderer op=on"]),
        ev.Finding(kind="bridge_api", value="library", source_command="analyze", notes=["members=loadTracks"]),
    ]
    bundle = ev.EvidenceBundle(target="/tmp/demo-app", findings=findings)
    plan = surface.classify(bundle)
    return plan, bundle


def test_generate_layout(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    assert (root / "DEMO-APP.md").exists()
    assert (root / "setup.py").exists()
    pkg = root / "cli_anything" / "demo_app"
    for rel in [
        "README.md",
        "__init__.py",
        "__main__.py",
        "demo_app_cli.py",
        "core/backend.py",
        "skills/SKILL.md",
        "tests/test_core.py",
        "utils/repl_skin.py",
    ]:
        assert (pkg / rel).exists(), rel
    assert (root / "FORGE-REPORT.md").exists()
    assert (root / "registry-entry.json").exists()


def test_registry_entry_fields(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    entry = json.loads((root / "registry-entry.json").read_text())
    for field in [
        "name", "display_name", "version", "description", "requires", "homepage",
        "source_url", "install_cmd", "entry_point", "skill_md", "category", "contributors",
    ]:
        assert field in entry
    assert entry["entry_point"] == "cli-anything-demo-app"


def test_backend_constants_baked_in(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    src = (root / "cli_anything" / "demo_app" / "core" / "backend.py").read_text()
    assert "library.db" in src
    assert "18110" in src
    assert "library:load" in src


def test_generated_cli_has_json_everywhere(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    src = (root / "cli_anything" / "demo_app" / "demo_app_cli.py").read_text()
    assert '"--json"' in src


def test_generated_tests_pass(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    report = vmod.validate(root, python=sys.executable)
    failed = [c for c in report.checks if not c.ok and c.name == "unit tests pass"]
    assert not failed, [c.detail for c in failed]


def test_validation_flags_placeholder_skin(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    report = vmod.validate(root, python=sys.executable)
    skin_check = next(c for c in report.checks if c.name == "repl_skin is a real copy")
    assert skin_check.ok is False  # placeholder detected, as designed
