"""Tests for the live verification loop."""

import json
import threading
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

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


def test_verify_database_file_with_uri_characters(tmp_path):
    db = tmp_path / "library#1.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE books(id INTEGER PRIMARY KEY)")
    bundle = ev.EvidenceBundle(target=str(db), findings=ev.local_filesystem_probe(db))
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="library")
    report = vfy.verify(bundle, plan, root)
    assert report.checks[0].state == "verified"
    assert "books" in report.checks[0].detail
    assert report.patches["TARGET_ROOT"] == str(tmp_path.resolve())


def test_verify_relative_rea_database_finding_for_file_target(tmp_path):
    db = tmp_path / "library.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE tracks(id INTEGER PRIMARY KEY)")
    # A real REA Windows envelope also exposes the file's basename. Its
    # regex-derived finding precedes the absolute local-filesystem finding.
    bundle = ev.EvidenceBundle(target=str(db), findings=[
        ev.Finding(kind="database", value=db.name, source_command="inspect-sqlite-database"),
        *ev.local_filesystem_probe(db),
    ])
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="library")
    report = vfy.verify(bundle, plan, root)
    assert report.checks[0].state == "verified"
    assert report.checks[0].target == str(db)
    assert report.patches["TARGET_ROOT"] == str(tmp_path.resolve())


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


def _live_gui(tmp_path, monkeypatch):
    from auto_cli import gui

    target = tmp_path / "wordpad.exe"
    target.write_bytes(b"target executable fixture")
    bundle = ev.EvidenceBundle(target=str(target))
    window = SimpleNamespace(ProcessId=42, NativeWindowHandle=123, ClassName="WordPadClass", Name="fixture.rtf")
    nodes = [gui.UiNode("fixture.rtf > Rich Text Window", "DocumentControl", "Rich Text Window", ["Text", "Value"], 1)]
    monkeypatch.setattr(gui, "window_executable", lambda pid: str(target))
    monkeypatch.setattr(gui, "capture_tree", lambda selected: nodes)
    monkeypatch.setattr(gui, "_find_node", lambda selected, path: object())
    monkeypatch.setattr(gui, "read_text", lambda control: "private document content")
    return bundle, window, gui


def test_gui_verification_reads_live_surface_and_binds_executable(tmp_path, monkeypatch):
    bundle, window, gui = _live_gui(tmp_path, monkeypatch)
    report = vfy.VerificationReport(bundle.target)
    capture = gui.GuiReport(process="WordPadClass")
    vfy.verify_gui(bundle, report, window, gui_report=capture)
    assert [check.kind for check in report.checks] == ["gui_window", "gui_controls", "gui_read"]
    assert report.counts == {"verified": 3}
    assert vfy.verified_gui_surface(report.to_dict(), bundle.target)
    assert capture.executable == bundle.target and capture.nodes
    assert report.checks[-1].evidence["characters"] == len("private document content")
    assert "private document content" not in json.dumps(report.to_dict())
    assert "editing and saving untested" in report.checks[-1].detail
    Path(bundle.target).write_bytes(b"different executable")
    assert not vfy.verified_gui_surface(report.to_dict(), bundle.target)


def test_gui_verification_rejects_another_application(tmp_path, monkeypatch):
    bundle, window, gui = _live_gui(tmp_path, monkeypatch)
    other = tmp_path / "other.exe"
    other.write_bytes(Path(bundle.target).read_bytes())
    monkeypatch.setattr(gui, "window_executable", lambda pid: str(other))
    monkeypatch.setattr(gui, "capture_tree", lambda window: pytest.fail("mismatched app must not be read"))
    report = vfy.verify_gui(bundle, vfy.VerificationReport(bundle.target), window)
    assert report.counts == {"failed": 1}
    assert "does not match" in report.checks[0].detail
    assert not vfy.verified_gui_surface(report.to_dict(), bundle.target)


def test_gui_failed_read_does_not_certify_workflow_surface(tmp_path, monkeypatch):
    bundle, window, gui = _live_gui(tmp_path, monkeypatch)
    monkeypatch.setattr(gui, "read_text", lambda node: (_ for _ in ()).throw(gui.GuiError("unsupported")))
    report = vfy.verify_gui(bundle, vfy.VerificationReport(bundle.target), window)
    assert report.checks[-1].kind == "gui_read" and report.checks[-1].state == "failed"
    assert not vfy.verified_gui_surface(report.to_dict(), bundle.target)


def test_gui_reverification_replaces_stale_gui_checks(tmp_path, monkeypatch):
    bundle, window, gui = _live_gui(tmp_path, monkeypatch)
    report = vfy.VerificationReport(bundle.target, [
        vfy.Verification("database", "books.db", "verified"),
        vfy.Verification("gui_read", "stale document", "verified"),
    ])
    vfy.verify_gui(bundle, report, window)
    assert report.checks[0].kind == "database"
    assert len(report.checks) == 4
    assert not any(check.target == "stale document" for check in report.checks)


def test_gui_identity_and_read_must_come_from_same_window(tmp_path, monkeypatch):
    bundle, window, gui = _live_gui(tmp_path, monkeypatch)
    report = vfy.verify_gui(bundle, vfy.VerificationReport(bundle.target), window)
    snapshot = report.to_dict()
    snapshot["checks"][-1]["evidence"]["window_id"] = 999
    assert not vfy.verified_gui_surface(snapshot, bundle.target)


def test_gui_verification_refuses_mismatched_bundle_report(tmp_path, monkeypatch):
    bundle, window, gui = _live_gui(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="does not match"):
        vfy.verify_gui(bundle, vfy.VerificationReport("another target"), window)
