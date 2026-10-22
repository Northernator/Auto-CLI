"""Tests for the live verification loop."""

import json
import threading
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from auto_cli import evidence as ev, scaffold, surface
from auto_cli import verify as vfy


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"tracks": [{"id": 1, "title": "x"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def live_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/api/tracks"
    srv.shutdown()


def _bundle_and_scaffold(tmp_path, url, with_db=True):
    findings = [ev.Finding(kind="endpoint", value=url, source_command="analyze")]
    if with_db:
        db = tmp_path / "target" / "library.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.executescript("CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT);")
        con.commit()
        con.close()
        findings.append(ev.Finding(kind="database", value="library.db", source_command="local-fs"))
    bundle = ev.EvidenceBundle(target=str(tmp_path / "target"), findings=findings)
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="live-app")
    return bundle, plan, root


def test_verify_live_endpoint(tmp_path, live_server):
    bundle, plan, root = _bundle_and_scaffold(tmp_path, live_server)
    report = vfy.verify(bundle, plan, root)
    eps = [c for c in report.checks if c.kind == "endpoint"]
    assert len(eps) == 1
    assert eps[0].state == "verified"
    assert "200" in eps[0].detail
    assert "tracks" in eps[0].detail


def test_verify_dead_endpoint_fails(tmp_path):
    bundle, plan, root = _bundle_and_scaffold(tmp_path, "http://127.0.0.1:1/nope")
    report = vfy.verify(bundle, plan, root)
    eps = [c for c in report.checks if c.kind == "endpoint"]
    assert eps[0].state == "failed"
    # dead port: either a real refusal, or a proxy answering 502/503/504
    assert "HTTP" in eps[0].detail or "no response" in eps[0].detail


def test_verify_database(tmp_path):
    bundle, plan, root = _bundle_and_scaffold(tmp_path, "http://127.0.0.1:1/x")
    report = vfy.verify(bundle, plan, root)
    dbs = [c for c in report.checks if c.kind == "database"]
    assert dbs[0].state == "verified"
    assert "tracks" in dbs[0].detail
    assert "TARGET_ROOT" in report.patches


def test_verify_patches_backend_endpoints(tmp_path, live_server):
    bundle, plan, root = _bundle_and_scaffold(tmp_path, live_server)
    vfy.verify(bundle, plan, root)
    backend = next(root.glob("cli_anything/*/core/backend.py")).read_text()
    assert live_server in backend


def test_verify_ipc_unverifiable(tmp_path):
    findings = [ev.Finding(kind="ipc_channel", value="lib:load", source_command="analyze")]
    bundle = ev.EvidenceBundle(target=str(tmp_path), findings=findings)
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="ipc-app")
    report = vfy.verify(bundle, plan, root)
    chans = [c for c in report.checks if c.kind == "ipc_channel"]
    assert chans[0].state == "unverifiable"


def test_report_written(tmp_path, live_server):
    bundle, plan, root = _bundle_and_scaffold(tmp_path, live_server)
    report = vfy.verify(bundle, plan, root)
    p = vfy.write_report(report, root)
    assert p.exists()
    text = p.read_text()
    assert "Verification report" in text
    assert "verified" in text
    assert (root / "verification.json").exists()
