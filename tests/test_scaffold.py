"""Tests for scaffold generation: layout, registry entry, and the generated
harness's own test suite actually passing."""

import json
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from auto_cli import evidence as ev
from auto_cli import scaffold, surface, validate as vmod


def _backend(root, module="demo_app"):
    path = root / "cli_anything" / module / "core" / "backend.py"
    spec = importlib.util.spec_from_file_location("generated_backend", path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


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


def test_database_paths_and_identifiers_are_quoted(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    backend = _backend(root)
    db = tmp_path / "library #1.db"
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE "odd"" table" ("song""title" TEXT)')
        con.execute('INSERT INTO "odd"" table" VALUES (?)', ("hello",))
    assert backend.db_info(db)["row_counts"] == {'odd" table': 1}
    assert backend.db_schema(db)['odd" table'][0]["name"] == 'song"title'
    assert backend.run_query(db, "SELECT 1")["rows"] == [[1]]


def test_export_does_not_truncate_large_tables(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    backend = _backend(root)
    db = tmp_path / "large.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE items (id INTEGER)")
        con.executemany("INSERT INTO items VALUES (?)", ((i,) for i in range(10001)))
    dest = tmp_path / "items.json"
    backend.export_table(db, "items", "json", str(dest))
    assert len(json.loads(dest.read_text(encoding="utf-8"))) == 10001


def test_backup_includes_committed_wal_data(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    backend = _backend(root)
    db = tmp_path / "wal.db"
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE items (value TEXT)")
        con.execute("INSERT INTO items VALUES ('committed in WAL')")
        con.commit()
        out = backend.backup_db(db)
        with sqlite3.connect(out) as backup:
            assert backup.execute("SELECT value FROM items").fetchall() == [("committed in WAL",)]
    finally:
        con.close()


def test_file_target_uses_containing_directory(tmp_path):
    db = tmp_path / "target.db"
    db.touch()
    bundle = ev.EvidenceBundle(target=str(db), findings=[
        ev.Finding(kind="database", value=db.name, source_command="local-fs")
    ])
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="demo-app")
    backend = _backend(root)
    assert backend.TARGET_ROOT == db.parent
    assert backend.resolve_db() == db


def test_missing_database_evidence_is_actionable(tmp_path):
    bundle = ev.EvidenceBundle(target=str(tmp_path))
    root = scaffold.generate(surface.classify(bundle), bundle, tmp_path / "out", name="demo-app")
    backend = _backend(root)
    with pytest.raises(backend.BackendError, match="no database discovered"):
        backend.resolve_db()


def test_validation_checks_cli_imports(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    cli = root / "cli_anything/demo_app/demo_app_cli.py"
    cli.write_text(cli.read_text(encoding="utf-8") + "\nimport auto_cli_missing_dependency\n", encoding="utf-8")
    report = vmod.validate(root, python=sys.executable)
    check = next(c for c in report.checks if c.name == "package imports")
    assert not check.ok
    assert "auto_cli_missing_dependency" in check.detail


def test_validation_reports_missing_interpreter(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    report = vmod.validate(root, python=str(tmp_path / "missing-python"))
    assert not next(c for c in report.checks if c.name == "package imports").ok
    assert not next(c for c in report.checks if c.name == "unit tests pass").ok
