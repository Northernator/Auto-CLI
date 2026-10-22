"""Normalize REA evidence envelopes into flat findings.

A finding is one machine-relevant fact about the target's automation surface,
with provenance (which REA command, which artifact sha256, which source
location) so a human can verify it before trusting the generated harness.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

LOOPBACK_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?[^\s\"'\\]*")
WEB_URL_RE = re.compile(r"https?://[^\s\"'<>()]{4,}")
SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
# Real IPC channels look like "library:load". REA's internal role_keys look
# like "argument:call:call:203:339:0" - same shape, zero signal. The sweep
# rejects anything with a numeric segment or deep nesting.
CHANNEL_RE = re.compile(r"\b([a-z][a-z0-9_-]*:[a-z][a-z0-9_:-]{2,})\b")
ROLE_KEY_NOISE = re.compile(r"(?::\d)|(?::[a-z-]+){3,}")
DB_FILE_RE = re.compile(r"[\w./-]+\.(?:sqlite|sqlite3|db)\b", re.IGNORECASE)


@dataclass
class Finding:
    kind: str  # ipc_channel | endpoint | bridge_api | database | webmcp | scripting | note
    value: str
    source_command: str = ""
    source_file: str = ""
    line: int | None = None
    confidence: str = ""
    artifact_sha256: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProbeRecord:
    command: str
    args: list[str]
    ok: bool
    error: dict | None = None
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "args": self.args,
            "ok": self.ok,
            "error": self.error,
            "duration_s": self.duration_s,
        }


@dataclass
class EvidenceBundle:
    target: str
    probes: list[ProbeRecord] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "probes": [p.to_dict() for p in self.probes],
            "findings": [f.to_dict() for f in self.findings],
            "limitations": self.limitations,
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "EvidenceBundle":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            target=doc["target"],
            probes=[ProbeRecord(**p) for p in doc.get("probes", [])],
            findings=[Finding(**f) for f in doc.get("findings", [])],
            limitations=doc.get("limitations", []),
        )


def _obs_evidence(obs: dict) -> tuple[str, str, str, int | None]:
    ev = obs.get("evidence") or {}
    conf = ev.get("confidence", "")
    sha = (ev.get("artifact") or {}).get("sha256", "")
    loc = (ev.get("location") or {}).get("value") or {}
    src = loc.get("source", "")
    line = (loc.get("start") or {}).get("line")
    return conf, sha, src, line


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.kind, f.value)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def extract_web_findings(command: str, envelope: dict) -> list[Finding]:
    """Findings from a REA browser-scenario capture.

    The capture envelope keeps the DOM inline per step; scripts and absolute
    URLs in it are the site's automation surface (API calls, bundles, MCP).
    """
    findings: list[Finding] = []
    nr = envelope.get("normalized_result") or {}
    for step in nr.get("steps") or []:
        arts = step.get("artifacts") or {}
        dom = (arts.get("dom") or {}).get("value") or {}
        text = dom.get("text") or ""
        sha = dom.get("sha256", "")
        url_info = (arts.get("url") or {}).get("value") or {}
        page_url = url_info.get("url", "")
        for u in set(WEB_URL_RE.findall(text)):
            u = u.rstrip(".,);")
            findings.append(
                Finding(
                    kind="endpoint",
                    value=u,
                    source_command=command,
                    source_file=page_url,
                    confidence="dom-capture",
                    artifact_sha256=sha,
                    notes=["from captured DOM"],
                )
            )
        for src in set(SCRIPT_SRC_RE.findall(text)):
            findings.append(
                Finding(
                    kind="note",
                    value=f"script bundle: {src}",
                    source_command=command,
                    source_file=page_url,
                    artifact_sha256=sha,
                )
            )
    blob = json.dumps(envelope).lower()
    if "webmcp" in blob:
        findings.append(
            Finding(kind="webmcp", value="page-declared WebMCP tools observed", source_command=command)
        )
    return _dedupe(findings)


def extract_findings(command: str, envelope: dict) -> list[Finding]:
    """Pull automation-surface facts out of one REA envelope."""
    findings: list[Finding] = []
    nr = envelope.get("normalized_result") or {}
    if nr.get("steps"):
        findings.extend(extract_web_findings(command, envelope))

    for node in (nr.get("graph") or {}).get("nodes") or []:
        kind = str(node.get("kind", ""))
        for obs in node.get("observations") or []:
            props = obs.get("properties") or {}
            label = obs.get("label", "")
            conf, sha, src, line = _obs_evidence(obs)

            if "ipc-channel" in kind:
                ch = props.get("channel") or label
                if ch and CHANNEL_RE.fullmatch(str(ch)):
                    findings.append(
                        Finding(
                            kind="ipc_channel",
                            value=str(ch),
                            source_command=command,
                            source_file=src,
                            line=line,
                            confidence=conf,
                            artifact_sha256=sha,
                            notes=[f"side={props.get('side', '?')} op={props.get('operation', '?')}"],
                        )
                    )
            elif kind == "endpoint" or props.get("endpoint_kind") == "network":
                val = props.get("value") or label
                if val:
                    notes = []
                    if props.get("mechanism"):
                        notes.append(f"mechanism={props['mechanism']}")
                    if props.get("resolution") and props["resolution"] != "literal":
                        notes.append(f"resolution={props['resolution']} (unresolved expression)")
                    findings.append(
                        Finding(
                            kind="endpoint",
                            value=str(val),
                            source_command=command,
                            source_file=src,
                            line=line,
                            confidence=conf,
                            artifact_sha256=sha,
                            notes=notes,
                        )
                    )
            elif "context-bridge" in kind:
                members = props.get("members") or []
                findings.append(
                    Finding(
                        kind="bridge_api",
                        value=str(props.get("api_key") or label),
                        source_command=command,
                        source_file=src,
                        line=line,
                        confidence=conf,
                        artifact_sha256=sha,
                        notes=[f"members={','.join(members) or '-'}"],
                    )
                )
            elif "webmcp" in kind.lower() or "mcp" in kind.lower():
                findings.append(
                    Finding(
                        kind="webmcp",
                        value=str(props.get("value") or label),
                        source_command=command,
                        source_file=src,
                        confidence=conf,
                        artifact_sha256=sha,
                    )
                )

    # Regex sweep over the whole envelope: catches facts the graph walk misses
    # (loopback URLs embedded in strings, db paths, webmcp mentions). Channels
    # are NOT swept - REA's envelope is full of internal identifiers that look
    # like channels ("call:fetch", "require:electron") but are not. Only
    # graph-derived ipc-channel nodes (REA's own extractor) are trusted.
    blob = json.dumps(envelope)
    for url in set(LOOPBACK_RE.findall(blob)):
        findings.append(Finding(kind="endpoint", value=url, source_command=command, confidence="regex-sweep"))
    for m in set(DB_FILE_RE.findall(blob)):
        findings.append(Finding(kind="database", value=m, source_command=command, confidence="regex-sweep"))
    for kw in ("webmcp", "WebMCP"):
        if kw.lower() in blob.lower() and not any(f.kind == "webmcp" for f in findings):
            findings.append(Finding(kind="webmcp", value=kw, source_command=command, confidence="regex-sweep"))

    # Envelope-level limitations are honest signal, keep them.
    for lim in envelope.get("limitations") or []:
        findings.append(Finding(kind="note", value=str(lim), source_command=command))

    return _dedupe(findings)


def local_filesystem_probe(target: str | Path) -> list[Finding]:
    """Cheap built-in probe: what ships inside the target directory.

    Not REA - auto-cli's own eyes. Finds DB files, ASARs, app bundles,
    plists, python entry points. These are starting points REA deepens.
    """
    root = Path(target)
    findings: list[Finding] = []
    if not root.exists():
        return findings
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        rel = str(p.relative_to(root))
        if name.endswith((".sqlite", ".sqlite3", ".db")):
            findings.append(Finding(kind="database", value=rel, source_command="local-fs", confidence="file-present"))
        elif name.endswith(".asar"):
            findings.append(Finding(kind="note", value=f"asar container: {rel}", source_command="local-fs"))
        elif name.endswith(".app") or name.endswith(".exe"):
            findings.append(Finding(kind="note", value=f"application binary: {rel}", source_command="local-fs"))
        elif name.endswith(".plist"):
            findings.append(Finding(kind="note", value=f"plist metadata: {rel}", source_command="local-fs"))
        elif name.endswith(".py") and "console" in p.read_text(errors="ignore").lower()[:200_000]:
            findings.append(Finding(kind="scripting", value=rel, source_command="local-fs", confidence="console-keyword"))
    return findings


def bundle_from_probes(
    target: str,
    probe_results: list,
    fs_findings: list[Finding] | None = None,
) -> EvidenceBundle:
    """Assemble a bundle from REA probe results (+ optional local fs probe)."""
    bundle = EvidenceBundle(target=target)
    for pr in probe_results:
        bundle.probes.append(
            ProbeRecord(
                command=pr.command,
                args=pr.args,
                ok=pr.ok,
                error=pr.error,
                duration_s=pr.duration_s,
            )
        )
        if pr.ok and pr.envelope:
            bundle.findings.extend(extract_findings(pr.command, pr.envelope))
            for lim in pr.envelope.get("limitations") or []:
                bundle.limitations.append(f"[{pr.command}] {lim}")
        elif pr.error:
            bundle.limitations.append(
                f"[{pr.command}] probe failed: {pr.error.get('code', 'error')} - "
                f"{pr.error.get('message', '')[:300]}"
            )
    if fs_findings:
        bundle.findings.extend(fs_findings)
    bundle.findings = _dedupe(bundle.findings)
    return bundle
