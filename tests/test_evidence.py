"""Tests for evidence extraction and bundle handling."""

import json
from pathlib import Path

import pytest

from auto_cli import evidence as ev

SYNTHETIC_ENVELOPE = {
    "evidence_id": "ev_123",
    "subject": {"name": "demo", "local_path": "/tmp/demo"},
    "normalized_result": {
        "graph": {
            "nodes": [
                {
                    "kind": "ipc-channel",
                    "observations": [
                        {
                            "label": "library:load",
                            "properties": {"channel": "library:load", "side": "renderer", "operation": "on"},
                            "evidence": {
                                "confidence": "exact",
                                "artifact": {"sha256": "abc"},
                                "location": {"value": {"source": "app/main.js", "start": {"line": 9}}},
                            },
                        }
                    ],
                },
                {
                    "kind": "endpoint",
                    "observations": [
                        {
                            "label": "network endpoint",
                            "properties": {
                                "endpoint_kind": "network",
                                "value": "http://127.0.0.1:18110/api/library/tracks",
                                "mechanism": "call:window.fetch",
                            },
                            "evidence": {"confidence": "exact", "artifact": {"sha256": "def"}},
                        }
                    ],
                },
                {
                    "kind": "context-bridge-api",
                    "observations": [
                        {
                            "label": "library",
                            "properties": {"api_key": "library", "members": ["loadTracks"]},
                            "evidence": {"confidence": "exact"},
                        }
                    ],
                },
            ]
        }
    },
    "limitations": ["one limitation"],
}


def test_extract_ipc_channel():
    findings = ev.extract_findings("analyze", SYNTHETIC_ENVELOPE)
    chans = [f for f in findings if f.kind == "ipc_channel"]
    assert any(f.value == "library:load" for f in chans)
    f = next(f for f in chans if f.value == "library:load")
    assert f.source_file == "app/main.js"
    assert f.line == 9
    assert f.artifact_sha256 == "abc"
    assert any("op=on" in n for n in f.notes)


def test_extract_endpoint():
    findings = ev.extract_findings("analyze", SYNTHETIC_ENVELOPE)
    eps = [f for f in findings if f.kind == "endpoint"]
    assert any(f.value == "http://127.0.0.1:18110/api/library/tracks" for f in eps)


def test_extract_bridge_api():
    findings = ev.extract_findings("analyze", SYNTHETIC_ENVELOPE)
    bridges = [f for f in findings if f.kind == "bridge_api"]
    assert any(f.value == "library" and "loadTracks" in f.notes[0] for f in bridges)


def test_limitations_become_notes():
    findings = ev.extract_findings("analyze", SYNTHETIC_ENVELOPE)
    assert any(f.kind == "note" and "one limitation" in f.value for f in findings)


def test_regex_sweep_catches_urls_missed_by_graph():
    envelope = {"normalized_result": {"graph": {"nodes": []}}}
    blob_finding = ev.Finding(kind="note", value="http://127.0.0.1:9999/x", source_command="x")
    doc = json.dumps({"normalized_result": {"graph": {"nodes": []}}, "extra": "http://127.0.0.1:9999/x"})
    findings = ev.extract_findings("probe", json.loads(doc))
    assert any(f.value == "http://127.0.0.1:9999/x" for f in findings)


def test_local_fs_probe(tmp_path):
    (tmp_path / "library.db").write_bytes(b"x")
    (tmp_path / "bundle.asar").write_bytes(b"x")
    findings = ev.local_filesystem_probe(tmp_path)
    kinds = {f.kind: f.value for f in findings}
    assert kinds.get("database") == "library.db"


def test_bundle_roundtrip(tmp_path):
    findings = ev.extract_findings("analyze", SYNTHETIC_ENVELOPE)
    bundle = ev.EvidenceBundle(
        target="/tmp/demo",
        probes=[ev.ProbeRecord(command="analyze", args=["/tmp/demo"], ok=True)],
        findings=findings,
    )
    p = tmp_path / "b.json"
    bundle.save(p)
    loaded = ev.EvidenceBundle.load(p)
    assert loaded.target == "/tmp/demo"
    assert len(loaded.findings) == len(bundle.findings)


def test_capability_failure_recorded_not_raised():
    from auto_cli import rea_client

    loc = rea_client.ReaLocation(["fake"], "test", "test")
    pr = rea_client.run_probe(loc, "inspect-sqlite-database", ["/x"], timeout=5)
    # command doesn't exist -> nonzero exit path; must be ok=False with error dict
    assert pr.ok is False
    assert pr.error is not None
