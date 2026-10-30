"""Tests for scaffold generation: layout, registry entry, and the generated
harness's own test suite actually passing."""

import json
import importlib.util
import os
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


def _cli(root, *args):
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "cli_anything.demo_app", *args],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


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


def test_default_repl_skin_is_a_licensed_real_copy_and_passes_validation(tmp_path, plan_and_bundle):
    import hashlib

    plan, bundle = plan_and_bundle
    root = scaffold.generate(plan, bundle, tmp_path, name="demo-app")
    utils = root / "cli_anything" / "demo_app" / "utils"
    vendor = Path(scaffold.__file__).parent / "_vendor"
    assert (utils / "repl_skin.py").read_bytes() == (vendor / "repl_skin.py").read_bytes()
    assert hashlib.sha256((utils / "repl_skin.py").read_bytes()).hexdigest() == "ae7c212948eb043bd8b23ad6eb63662b5d5b3b00aa52cf655033da1b90b9e583"
    assert (utils / "LICENSE-CLI-Anything.txt").read_bytes() == (vendor / "LICENSE.txt").read_bytes()
    assert '"utils/LICENSE*.txt"' in (root / "setup.py").read_text()
    report = vmod.validate(root, python=sys.executable)
    skin_check = next(c for c in report.checks if c.name == "repl_skin is a real copy")
    assert skin_check.ok is True


def test_explicit_repl_skin_override_preserves_bytes(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    skin = tmp_path / "custom.py"
    skin.write_bytes(b"# explicit custom skin\r\nclass ReplSkin: pass\r\n")
    root = scaffold.generate(plan, bundle, tmp_path / "generated", name="demo-app", repl_skin=skin)
    utils = root / "cli_anything" / "demo_app" / "utils"
    assert (utils / "repl_skin.py").read_bytes() == skin.read_bytes()
    assert not (utils / "LICENSE-CLI-Anything.txt").exists()


def test_missing_explicit_repl_skin_fails_before_generating_files(tmp_path, plan_and_bundle):
    plan, bundle = plan_and_bundle
    output = tmp_path / "generated"
    with pytest.raises(FileNotFoundError, match="REPL skin file not found"):
        scaffold.generate(plan, bundle, output, name="demo-app", repl_skin=tmp_path / "missing.py")
    assert not output.exists()


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


def test_info_without_database_reports_target_runtime_and_capabilities(tmp_path):
    target = tmp_path / "sample.exe"
    target.write_bytes(b"fixture")
    bundle = ev.EvidenceBundle(target=str(target))
    root = scaffold.generate(surface.classify(bundle), bundle, tmp_path / "out", name="demo-app")
    proc = _cli(root, "--json", "info")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["target"]["path"] == str(target.resolve())
    assert result["target"]["exists"] is True
    assert result["runtime"]["executable"]
    assert result["capabilities"]["database"]["discovered"] is False
    assert result["capabilities"]["ipc"]["transport_implemented"] is False
    assert result["evidence"]["live_checked_by_info"] is False


def test_database_info_preserves_default_and_explicit_paths(tmp_path):
    db = tmp_path / "library.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE items (name TEXT)")
        con.execute("INSERT INTO items VALUES ('one')")
    bundle = ev.EvidenceBundle(target=str(tmp_path), findings=[
        ev.Finding(kind="database", value=db.name, source_command="local-fs")
    ])
    root = scaffold.generate(surface.classify(bundle), bundle, tmp_path / "out", name="demo-app")
    for args in [(), (str(db),), ("--db", str(db))]:
        proc = _cli(root, "--json", "info", *args)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["row_counts"] == {"items": 1}
    proc = _cli(root, "--json", "info", str(db), "--db", str(db))
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("args,code", [
    (("unknown-command",), "invalid_input"),
    (("query",), "invalid_input"),
    (("query", "SELECT 1", "--limit", "invalid"), "invalid_input"),
    (("schema",), "backend_error"),
    (("get", "http://["), "backend_error"),
    (("send", "unknown-channel"), "backend_error"),
])
def test_json_errors_include_parse_and_backend_failures(tmp_path, args, code):
    bundle = ev.EvidenceBundle(target=str(tmp_path))
    root = scaffold.generate(surface.classify(bundle), bundle, tmp_path / "out", name="demo-app")
    proc = _cli(root, "--json", *args)
    assert proc.returncode == 2, proc.stderr
    result = json.loads(proc.stdout)
    assert result["ok"] is False
    assert result["error"]["code"] == code
    assert result["error"]["message"]
    assert proc.stderr == ""


def test_generated_documentation_uses_local_install_and_available_commands(tmp_path):
    bundle = ev.EvidenceBundle(target=str(tmp_path))
    plan = surface.classify(bundle)
    plan.commands.append(surface.CommandSpec("eval", "not implemented by the base scaffold"))
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="demo-app")
    for path in [root / "README.md", root / "cli_anything/demo_app/README.md", root / "cli_anything/demo_app/skills/SKILL.md"]:
        text = path.read_text(encoding="utf-8")
        assert "python -m pip install ." in text
        assert "cli-anything-demo-app --json info" in text
        assert "<you>" not in text
        assert "git+https://github.com/HKUDS/CLI-Anything" not in text
        assert "cli-anything-demo-app eval" not in text
        assert "unit tests" in text and "workflow" in text
    entry = json.loads((root / "registry-entry.json").read_text(encoding="utf-8"))
    assert entry["install_cmd"] == "python -m pip install ."


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
