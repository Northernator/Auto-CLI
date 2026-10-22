"""Tests for evidence-driven completion (complete command)."""

import json
import importlib.util
import os
import sqlite3
import subprocess
import sys

import pytest

from auto_cli import complete as cmp_mod, evidence as ev, scaffold, surface


@pytest.fixture()
def built(tmp_path):
    db = tmp_path / "target" / "library.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT, artist TEXT, bpm REAL);"
        "CREATE TABLE playlists (id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO tracks (title, artist, bpm) VALUES ('Solar Falls', 'Nova Ray', 124.0);"
    )
    con.commit()
    con.close()
    findings = [
        ev.Finding(kind="database", value="library.db", source_command="local-fs"),
        ev.Finding(kind="endpoint", value="http://127.0.0.1:18110/api/library/tracks", source_command="analyze"),
    ]
    bundle = ev.EvidenceBundle(target=str(tmp_path / "target"), findings=findings)
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="done-app")
    return bundle, plan, root


def test_complete_generates_table_groups(tmp_path, built):
    bundle, plan, root = built
    report = cmp_mod.complete(bundle, plan, root)
    assert set(report.tables) == {"tracks", "playlists"}
    cli_src = (root / "cli_anything" / "done_app" / "done_app_cli.py").read_text()
    assert "def tracks_list" in cli_src
    assert "def playlists_search" in cli_src
    assert "--yes" in cli_src  # writes gated
    backend = (root / "cli_anything" / "done_app" / "core" / "backend.py").read_text()
    assert "def table_add" in backend
    assert "mode=ro" in backend


def test_complete_endpoint_commands_from_verification(tmp_path, built):
    bundle, plan, root = built
    verification = {
        "checks": [
            {"kind": "endpoint", "state": "verified", "target": "http://127.0.0.1:18110/api/library/tracks", "detail": "200 json"},
            {"kind": "endpoint", "state": "failed", "target": "http://127.0.0.1:18110", "detail": "404"},
        ]
    }
    report = cmp_mod.complete(bundle, plan, root, verification)
    assert report.endpoint_commands == ["library-tracks"]
    cli_src = (root / "cli_anything" / "done_app" / "done_app_cli.py").read_text()
    assert "def library_tracks(" in cli_src
    # the failed endpoint got no command
    assert "def root(" not in cli_src


def test_complete_without_verification_skips_endpoints(tmp_path, built):
    bundle, plan, root = built
    report = cmp_mod.complete(bundle, plan, root)
    assert report.endpoint_commands == []


def test_complete_is_idempotent(tmp_path, built):
    bundle, plan, root = built
    cmp_mod.complete(bundle, plan, root)
    cli = root / "cli_anything" / "done_app" / "done_app_cli.py"
    first = cli.read_text()
    cmp_mod.complete(bundle, plan, root)
    assert cli.read_text() == first


def test_generated_table_tests_pass(tmp_path, built):
    bundle, plan, root = built
    cmp_mod.complete(bundle, plan, root)
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(root / "cli_anything/done_app/tests/test_core.py"), "-q", "--no-header"],
        capture_output=True,
        text=True,
        timeout=300,
        env={"PYTHONPATH": str(root), "PATH": "C:\\Windows\\System32", "SYSTEMROOT": "C:\\Windows", "TEMP": tmpdir(), "TMP": tmpdir()},
    )
    assert proc.returncode == 0, proc.stdout[-2000:]


def test_skill_documents_surface(tmp_path, built):
    bundle, plan, root = built
    cmp_mod.complete(bundle, plan, root)
    skill = (root / "cli_anything" / "done_app" / "skills" / "SKILL.md").read_text()
    assert "Generated domain surface" in skill
    assert "tracks list" in skill


def test_endpoint_command_name():
    assert cmp_mod._endpoint_command_name("http://127.0.0.1:18110/api/library/tracks") == "library-tracks"
    assert cmp_mod._endpoint_command_name("http://127.0.0.1:18110/") == "root"


def test_completion_decodes_paths_and_handles_sqlite_names(tmp_path):
    target = tmp_path / "target #1"
    db = target / "data" / "library's.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as con:
        con.executescript('''
            CREATE TABLE "track-list" (id INTEGER PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL);
            CREATE TABLE info (id INTEGER PRIMARY KEY);
            CREATE TABLE class (id INTEGER PRIMARY KEY);
            CREATE TABLE "123notes" (id INTEGER PRIMARY KEY);
            CREATE TABLE "odd""table" ("song""title" TEXT);
            INSERT INTO "odd""table" VALUES ('hello');
        ''')
    con.close()
    bundle = ev.EvidenceBundle(target="not-the-database-root", findings=[
        ev.Finding(kind="database", value=str(db.relative_to(target)), source_command="local-fs")
    ])
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="done-app", target_root=target)
    report = cmp_mod.complete(bundle, plan, root)
    assert set(report.tables) == {"track-list", "info", "class", "123notes", 'odd"table'}
    assert report.table_commands["info"] == "info-rows"
    assert report.table_commands["class"] == "table-class"
    backend_path = root / "cli_anything/done_app/core/backend.py"
    spec = importlib.util.spec_from_file_location("completed_backend", backend_path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    assert backend.table_search(db, 'odd"table', "hello")["matches"] == [{'song"title': "hello"}]
    backend.table_add(db, 'odd"table', {'song"title': "new"})
    assert len(backend.table_list(db, 'odd"table')["rows"]) == 2
    with pytest.raises(backend.BackendError, match="JSON object"):
        backend.table_update(db, 'odd"table', 1, [])
    # Generated unit tests remain runnable after the target app is unavailable.
    db.rename(db.with_suffix(".unavailable"))
    env = dict(os.environ, PYTHONPATH=str(root))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(root / "cli_anything/done_app/tests/test_core.py"), "-q"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_generated_update_rejects_non_object_before_backup(built):
    bundle, plan, root = built
    cmp_mod.complete(bundle, plan, root)
    proc = subprocess.run(
        [sys.executable, "-m", "cli_anything.done_app", "tracks", "update", "1", "--payload", "[]", "--yes"],
        capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PYTHONPATH=str(root)),
    )
    assert proc.returncode == 2
    assert "JSON object" in proc.stderr
    assert not list((root / "..").rglob("*.backup-*"))


def test_generated_database_failure_is_a_cli_error(built):
    bundle, plan, root = built
    cmp_mod.complete(bundle, plan, root)
    proc = subprocess.run(
        [sys.executable, "-m", "cli_anything.done_app", "tracks", "add", "--payload", '{"unknown": 1}', "--yes"],
        capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PYTHONPATH=str(root)),
    )
    assert proc.returncode == 2
    assert "unknown columns" in proc.stderr
    assert "Traceback" not in proc.stderr


def tmpdir():
    import tempfile

    return tempfile.gettempdir()
