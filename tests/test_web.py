"""Tests for website (URL) recon: scenario building, DOM extraction,
origin-scoped guards."""

import json

from auto_cli import cli, evidence as ev, scaffold, surface

WEB_ENVELOPE = {
    "normalized_result": {
        "steps": [
            {
                "step_id": "settle",
                "action": "wait_for_timeout",
                "artifacts": {
                    "dom": {
                        "state": "captured",
                        "value": {
                            "sha256": "deadbeef",
                            "text": (
                                '<html><body>'
                                '<script src="/static/app.js"></script>'
                                '<a href="https://example.org/docs">docs</a>'
                                '<script>fetch("https://api.example.com/v1/tracks")</script>'
                                "</body></html>"
                            ),
                        },
                    },
                    "url": {"state": "captured", "value": {"url": "https://example.com/", "origin": "https://example.com"}},
                },
            }
        ]
    },
    "limitations": ["network bodies not retained"],
}


def test_build_scenario_shape():
    s = cli.build_scenario("https://x.test", "C:/chrome.exe", 1234)
    assert s["browser"] == {"mode": "launch", "executable_path": "C:/chrome.exe"}
    assert s["start_url"] == {"url": "https://x.test"}
    assert s["actions"][0]["action"] == "wait_for_timeout"
    assert s["actions"][0]["duration_ms"] == 1234
    assert "dom" in s["capture"]["at_end"]


def test_extract_web_findings():
    findings = ev.extract_findings("capture-browser-scenario", WEB_ENVELOPE)
    urls = {f.value for f in findings if f.kind == "endpoint"}
    assert "https://api.example.com/v1/tracks" in urls
    assert "https://example.org/docs" in urls
    scripts = [f.value for f in findings if f.kind == "note" and "script bundle" in f.value]
    assert any("/static/app.js" in s for s in scripts)


def test_web_classification_flags_remote_urls():
    bundle = ev.EvidenceBundle(
        target="https://example.com",
        findings=ev.extract_findings("capture-browser-scenario", WEB_ENVELOPE),
    )
    plan = surface.classify(bundle)
    # remote-only endpoints must NOT claim local-server flavor
    assert "local-server" not in plan.flavors
    assert any("outbound links are not API surface" in r for r in plan.risks)
    assert any("api.example.com" in e for e in plan.evidence)


def test_loopback_still_local_server():
    b = ev.EvidenceBundle(
        target="/app",
        findings=[ev.Finding(kind="endpoint", value="http://127.0.0.1:18110/api/x")],
    )
    plan = surface.classify(b)
    assert plan.primary_flavor == "local-server"


def test_generated_backend_origin_scoped(tmp_path):
    bundle = ev.EvidenceBundle(
        target="https://example.com",
        findings=ev.extract_findings("capture-browser-scenario", WEB_ENVELOPE),
    )
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path, name="example-site", target_root=tmp_path)
    src = (root / "cli_anything" / "example_site" / "core" / "backend.py").read_text()
    assert "ALLOWED_ORIGINS" in src
    assert "api.example.com" in src
    assert "outside discovered origins" in src


def test_find_target_id_digs_recursively():
    doc = {"normalized_result": {"targets": [{"title": "x", "targetId": "ABC123DEF"}]}}
    assert cli._find_target_id(doc, "https://x") == "ABC123DEF"
