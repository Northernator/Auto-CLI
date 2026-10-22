"""Tests for evidence-driven completion (complete command)."""

import json
import sqlite3

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


def tmpdir():
    import tempfile

    return tempfile.gettempdir()
