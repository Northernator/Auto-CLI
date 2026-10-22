"""Tests for surface classification."""

from auto_cli import evidence as ev
from auto_cli import surface


def _bundle(*findings) -> ev.EvidenceBundle:
    return ev.EvidenceBundle(target="/tmp/demo", findings=list(findings))


def test_db_flavor_requires_backup():
    b = _bundle(ev.Finding(kind="database", value="library.db"))
    plan = surface.classify(b)
    assert "database" in plan.flavors
    assert plan.backup_required is True
    names = [c.name for c in plan.commands]
    assert {"info", "schema", "backup", "query"} <= set(names)
    assert any("SQLCipher" in t for t in plan.human_todos)


def test_loopback_endpoint_gives_local_server():
    b = _bundle(ev.Finding(kind="endpoint", value="http://127.0.0.1:18110/api/x"))
    plan = surface.classify(b)
    assert "local-server" in plan.flavors
    assert plan.primary_flavor == "local-server"
    assert "get" in [c.name for c in plan.commands]


def test_webmcp_wins_primary():
    b = _bundle(
        ev.Finding(kind="webmcp", value="search"),
        ev.Finding(kind="endpoint", value="http://127.0.0.1:1/x"),
    )
    plan = surface.classify(b)
    assert plan.primary_flavor == "mcp-face"


def test_ipc_channels():
    b = _bundle(
        ev.Finding(kind="ipc_channel", value="library:load", notes=["side=renderer op=on"]),
        ev.Finding(kind="ipc_channel", value="library:loaded", notes=["side=renderer op=send"]),
    )
    plan = surface.classify(b)
    assert "local-server" in plan.flavors
    assert "channels" in [c.name for c in plan.commands]
    assert any("impersonate the renderer" in r for r in plan.risks)


def test_nothing_found_is_honest():
    plan = surface.classify(_bundle())
    assert plan.primary_flavor == "unknown"
    assert plan.human_todos


def test_unresolved_endpoint_flagged():
    b = _bundle(
        ev.Finding(
            kind="endpoint",
            value="BACKEND + '/api/x'",
            notes=["resolution=partial (unresolved expression)"],
        )
    )
    plan = surface.classify(b)
    assert any("inferred" in r for r in plan.risks)
