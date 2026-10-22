"""Classify findings into a CLI-Anything harness plan.

The three CLI-Anything harness flavors:
  1. scripting-runtime - the app has a console (Python/JS), build the CLI on it
  2. local-server      - the app exposes a loopback API, speak HTTP to it
  3. mcp-face          - an MCP server already exists, give it a CLI face
Plus the database flavor the registry learned from Rekordbox: the app owns a
local data file - wrap it read-only by default, backup before any write.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from urllib.parse import urlsplit

from .evidence import EvidenceBundle

FLAVORS = ("scripting-runtime", "local-server", "mcp-face", "database", "unknown")


@dataclass
class CommandSpec:
    name: str
    purpose: str
    mutating: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HarnessPlan:
    target: str
    primary_flavor: str
    flavors: list[str]
    commands: list[CommandSpec]
    backup_required: bool
    risks: list[str]
    evidence: list[str]
    human_todos: list[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def summary_lines(self) -> list[str]:
        lines = [
            f"target: {self.target}",
            f"primary flavor: {self.primary_flavor}",
            f"flavors: {', '.join(self.flavors)}",
            f"backup-before-write: {'required' if self.backup_required else 'not required'}",
            "suggested commands:",
        ]
        for c in self.commands:
            tag = " [mutating]" if c.mutating else ""
            lines.append(f"  {c.name:<12} {c.purpose}{tag}")
        if self.risks:
            lines.append("risks:")
            lines.extend(f"  - {r}" for r in self.risks)
        if self.human_todos:
            lines.append("human todos before this harness can merge:")
            lines.extend(f"  - {t}" for t in self.human_todos)
        return lines


def _endpoint_urls(bundle: EvidenceBundle) -> list[str]:
    urls = []
    for f in bundle.of_kind("endpoint"):
        v = f.value
        if v.startswith(("http://", "https://")):
            urls.append(v)
        elif "127.0.0.1" in v or "localhost" in v:
            urls.append("http://" + v.lstrip("/"))
    # dedupe preserving order
    seen, out = set(), []
    for u in urls:
        base = u.split("?")[0]
        if base not in seen:
            seen.add(base)
            out.append(u)
    return out


def _is_loopback(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def _loopback_ports(bundle: EvidenceBundle) -> list[int]:
    ports = set()
    for u in _endpoint_urls(bundle):
        try:
            p = urlsplit(u).port
            if p:
                ports.add(p)
        except ValueError:
            pass
    return sorted(ports)


def classify(bundle: EvidenceBundle) -> HarnessPlan:
    flavors: set[str] = set()
    commands: list[CommandSpec] = []
    risks: list[str] = []
    todos: list[str] = []
    evidence: list[str] = []

    channels = bundle.of_kind("ipc_channel")
    bridges = bundle.of_kind("bridge_api")
    dbs = bundle.of_kind("database")
    webmcp = bundle.of_kind("webmcp")
    scripting = bundle.of_kind("scripting")

    for f in channels:
        evidence.append(f"ipc channel '{f.value}' ({f.source_command}, {f.source_file}:{f.line or '?'})")
    for f in bridges:
        evidence.append(f"context-bridge api '{f.value}' ({f.source_command})")
    for u in _endpoint_urls(bundle):
        label = "loopback endpoint" if _is_loopback(u) else "remote URL (unverified)"
        evidence.append(f"{label} {u}")
    for f in dbs:
        evidence.append(f"database file '{f.value}' ({f.source_command})")

    if webmcp:
        flavors.add("mcp-face")
        commands.append(CommandSpec("tools", "list discovered WebMCP tools"))
        commands.append(CommandSpec("call", "invoke a discovered tool by name"))
        todos.append("verify each discovered WebMCP tool still exists; wrap it 1:1, do not invent tools")

    all_urls = _endpoint_urls(bundle)
    loopback = [u for u in all_urls if _is_loopback(u)]
    remote = [u for u in all_urls if not _is_loopback(u)]

    if loopback:
        flavors.add("local-server")
        commands.append(CommandSpec("get", "GET one discovered endpoint, print body"))
        commands.append(CommandSpec("endpoints", "list the endpoint map with provenance"))
        ports = _loopback_ports(bundle)
        if ports:
            risks.append(f"loopback port(s) {ports} must be confirmed against the app's own docs")

    if remote:
        origins = sorted({urlsplit(u).netloc for u in remote if urlsplit(u).netloc})
        risks.append(
            "remote URLs found in the page DOM ("
            + ", ".join(origins[:5])
            + ") - outbound links are not API surface; confirm which are real XHR/fetch targets"
        )
        todos.append(
            "run analyze-javascript-application on the exported page scripts to separate API calls from links"
        )

    if any(
        "unresolved expression" in n for f in bundle.of_kind("endpoint") for n in f.notes
    ):
        risks.append(
            "some endpoints are unresolved source expressions - URL is inferred, verify before trusting"
        )
        todos.append("capture a HAR of the real app and diff it against the discovered endpoint list")

    if channels or bridges:
        flavors.add("local-server")  # IPC is driven over loopback transports
        commands.append(CommandSpec("channels", "list discovered IPC channels with side/operation"))
        commands.append(CommandSpec("send", "send one literal channel message (dry-run by default)"))
        if any(f.confidence == "regex-sweep" for f in channels):
            risks.append("some channels found by regex sweep only - confirm they are real IPC channels")
        if channels and not any("op=on" in (f.notes[0] if f.notes else "") for f in channels):
            todos.append("confirm which side (main vs renderer) owns each channel before wiring handlers")

    if dbs:
        flavors.add("database")
        commands.append(CommandSpec("info", "read-only DB overview: path, size, page count"))
        commands.append(CommandSpec("schema", "read-only table/column inventory"))
        commands.append(CommandSpec("backup", "copy the DB to a timestamped file before any write"))
        commands.append(CommandSpec("query", "read-only SELECT with row limit"))
        commands.append(CommandSpec("export", "export a table to CSV/JSON", mutating=False))
        todos.append("check whether the DB is encrypted (SQLCipher) - if so, guarded paths only, like the Rekordbox harness")
        todos.append("close-copy the DB before any write path; never mutate in place")

    if scripting:
        flavors.add("scripting-runtime")
        commands.append(CommandSpec("eval", "run a bounded expression in the app's console (guarded)"))
        todos.append("confirm the console is reachable headless before shipping eval")

    if not flavors:
        flavors.add("unknown")
        commands.append(CommandSpec("info", "placeholder: describe what REA found"))
        todos.append("REA found no automation surface - run deeper probes (analyze-web-bundle, capture-process) before writing a harness")

    primary = "unknown"
    for f in ("mcp-face", "local-server", "database", "scripting-runtime"):
        if f in flavors:
            primary = f
            break

    backup_required = bool(dbs)

    if any("op=send" in (f.notes[0] if f.notes else "") for f in channels):
        risks.append("renderer-side send channels exist - a CLI must not impersonate the renderer without the app running")

    return HarnessPlan(
        target=bundle.target,
        primary_flavor=primary,
        flavors=sorted(flavors),
        commands=commands,
        backup_required=backup_required,
        risks=risks,
        evidence=evidence,
        human_todos=todos,
    )
