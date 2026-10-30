"""Opt-in tests against real REA and installed applications, without mocks.

See docs/ci.md for pinned dependencies and the coverage boundary.
"""

import importlib.util
from importlib.metadata import version
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from auto_cli import complete, evidence, rea_client, scaffold, surface, verify


pytestmark = pytest.mark.skipif(
    os.environ.get("AUTO_CLI_REAL_APPS") != "1",
    reason="set AUTO_CLI_REAL_APPS=1 and install the dependencies in docs/ci.md",
)

TRACKS = [{"id": 1, "title": "First track"}, {"id": 2, "title": "Second track"}]


def _run(argv, *, cwd=None, expected=0):
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=90,
    )
    assert result.returncode == expected, (
        f"{argv!r} exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    return result


@pytest.fixture(scope="module")
def rea():
    for module in ("datasette", "sqlite_utils"):
        if importlib.util.find_spec(module) is None:
            pytest.fail("real-app tests enabled: install .github/requirements-integration.txt")
    entry = os.environ.get("REA_BIN", "")
    if not entry or not Path(entry).is_file():
        pytest.fail("real-app tests enabled: set REA_BIN to the installed scripts/rea.mjs; see docs/ci.md")
    node = shutil.which("node")
    if not node:
        pytest.fail("real-app tests enabled: install Node.js; see docs/ci.md")
    loc = rea_client.ReaLocation([node, str(Path(entry).resolve())], "env", "pinned integration REA")
    assert rea_client.version(loc), "REA --version returned no version"
    return loc


@pytest.fixture()
def workspace(tmp_path, request, rea):
    destination = os.environ.get("AUTO_CLI_TEST_ARTIFACTS")
    root = Path(destination) / request.node.name if destination else tmp_path
    root.mkdir(parents=True, exist_ok=True)
    (root / "tool-versions.json").write_text(json.dumps({
        "rea": rea_client.version(rea),
        "node": _run([rea.argv[0], "--version"]).stdout.strip(),
        "python": sys.version,
        "datasette": version("datasette"),
        "sqlite-utils": version("sqlite-utils"),
    }, indent=2), encoding="utf-8")
    return root


def _database(root):
    """Create the target database through the actual sqlite-utils CLI."""
    db = root / "library.db"
    source = root / "tracks.json"
    source.write_text(json.dumps(TRACKS), encoding="utf-8")
    _run([sys.executable, "-m", "sqlite_utils", "insert", str(db), "tracks", str(source), "--pk", "id"])
    return db


def _probe(rea, command, args, root):
    result = rea_client.run_probe(rea, command, args, timeout=120)
    (root / f"{command}.json").write_text(
        json.dumps(result.envelope if result.ok else result.error, indent=2), encoding="utf-8"
    )
    assert result.ok, f"real REA {command} failed: {result.error}"
    return result


def _generate(bundle, root, name):
    bundle.save(root / "evidence-bundle.json")
    plan = surface.classify(bundle)
    harness = scaffold.generate(plan, bundle, root / "generated", name=name)
    report = verify.verify(bundle, plan, harness)
    verify.write_report(report, harness)
    completion = complete.complete(bundle, plan, harness, report.to_dict())
    return harness, report, completion


def _cli(harness, module, *args, expected=0):
    result = _run(
        [sys.executable, "-m", f"cli_anything.{module}", "--json", *args],
        cwd=harness, expected=expected,
    )
    return json.loads(result.stdout) if expected == 0 else result


def test_sqlite_utils_recon_to_generated_workflow(rea, workspace):
    db = _database(workspace)
    probe = _probe(rea, "inspect-sqlite-database", [str(db), "--schema-scope", "database"], workspace)
    assert "tracks" in json.dumps(probe.envelope)
    bundle = evidence.bundle_from_probes(str(db), [probe], evidence.local_filesystem_probe(db))
    harness, report, completion = _generate(bundle, workspace, "sqlite-app")
    assert any(check.kind == "database" and check.state == "verified" for check in report.checks)
    assert "tracks" in completion.tables
    assert _cli(harness, "sqlite_app", "info")["row_counts"]["tracks"] == 2
    assert _cli(harness, "sqlite_app", "tracks", "get", "1") == TRACKS[0]
    assert _cli(harness, "sqlite_app", "tracks", "search", "Second")["matches"] == [TRACKS[1]]

    payload = json.dumps({"id": 3, "title": "From the generated harness"})
    denied = _cli(harness, "sqlite_app", "tracks", "add", "--payload", payload, expected=2)
    assert "without --yes" in denied.stderr
    assert not list(workspace.glob("*.backup-*.db"))
    inserted = _cli(harness, "sqlite_app", "tracks", "add", "--payload", payload, "--yes")
    assert inserted["inserted"] == 3
    backup = Path(inserted["backup"])
    assert backup.is_file()
    for path, count in ((db, 3), (backup, 2)):
        result = _run([sys.executable, "-m", "sqlite_utils", str(path), "SELECT COUNT(*) AS count FROM tracks"])
        assert json.loads(result.stdout) == [{"count": count}]


@pytest.fixture()
def datasette_server(workspace):
    db = _database(workspace)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}/library/tracks.json"
    log_path = workspace / "datasette.log"
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "datasette", str(db), "--host", "127.0.0.1", "--port", str(port)],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    pytest.fail(f"Datasette exited during startup: {log_path.read_text(encoding='utf-8')}")
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(f"Datasette did not become ready: {log_path.read_text(encoding='utf-8')}")
            yield url
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)


def test_datasette_discovered_endpoint_to_generated_command(rea, workspace, datasette_server):
    # A small client supplies an absolute endpoint for static discovery. The
    # server is the real Datasette app, not a stub or an in-process fake.
    client = workspace / "client"
    client.mkdir()
    (client / "index.js").write_text(f"fetch({json.dumps(datasette_server)});\n", encoding="utf-8")
    probe = _probe(rea, "analyze-javascript-application", [str(client)], workspace)
    bundle = evidence.bundle_from_probes(str(client), [probe])
    assert any(f.value == datasette_server for f in bundle.of_kind("endpoint"))
    harness, report, completion = _generate(bundle, workspace, "datasette-app")
    assert any(c.target == datasette_server and c.state == "verified" for c in report.checks)
    assert len(completion.endpoint_commands) == 1
    result = _cli(harness, "datasette_app", completion.endpoint_commands[0])
    assert result["database"] == "library"
    assert result["table"] == "tracks"
    assert result["columns"] == ["id", "title"]
    assert result["rows"] == [[1, "First track"], [2, "Second track"]]
