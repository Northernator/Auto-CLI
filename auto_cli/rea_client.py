"""Locate and drive REA (https://github.com/morluto/rea, npm: rea-agents).

REA is treated as an external dependency: a global `rea` install, a path in
$REA_BIN, or an npx fallback. Every probe runs with `--format json` and the
full envelope is preserved. REA capability failures come back as structured
JSON (code: capability_unavailable) - those are recorded as probe failures
with remediation text, never raised.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_NPX_PACKAGE = "rea-agents@latest"


@dataclass
class ReaLocation:
    argv: list[str]
    kind: str  # "global" | "env" | "npx"
    detail: str


def _npx_candidates() -> list[list[str]]:
    """npx on Windows is npx.cmd - CreateProcess cannot run it bare."""
    cands: list[list[str]] = []
    for name in ("npx.cmd", "npx.exe"):
        w = shutil.which(name)
        if w:
            cands.append([w])
    w = shutil.which("npx")
    if w:
        cands.append([w])
        cands.append(["cmd", "/c", "npx"])
    return cands


def find_rea() -> ReaLocation | None:
    """First REA invocation that actually answers --version wins."""
    candidates: list[ReaLocation] = []
    env = os.environ.get("REA_BIN")
    if env and Path(env).exists():
        argv = [env]
        # a .mjs/.js checkout entry needs node in front of it to be runnable
        if env.endswith((".mjs", ".js")):
            node = shutil.which("node") or "node"
            argv = [node, env]
        candidates.append(ReaLocation(argv, "env", f"REA_BIN={env}"))
    which = shutil.which("rea")
    if which:
        candidates.append(ReaLocation([which], "global", which))
    for argv in _npx_candidates():
        candidates.append(ReaLocation(argv + ["-y", DEFAULT_NPX_PACKAGE], "npx", "npx fallback"))
    for loc in candidates:
        try:
            if version(loc).strip():
                return loc
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    return None


def version(loc: ReaLocation, timeout: int = 120) -> str:
    proc = subprocess.run(
        loc.argv + ["--version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    proc.check_returncode()
    return proc.stdout.strip()


@dataclass
class ProbeResult:
    command: str
    args: list[str]
    ok: bool
    envelope: dict | None = None
    error: dict | None = None
    duration_s: float = 0.0
    raw: str = ""

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "args": self.args,
            "ok": self.ok,
            "duration_s": round(self.duration_s, 2),
            "error": self.error,
        }


def run_probe(
    loc: ReaLocation,
    command: str,
    args: list[str],
    timeout: int = 600,
) -> ProbeResult:
    """Run one REA command with JSON output. Never raises on REA errors."""
    import time

    argv = loc.argv + [command] + list(args) + ["--format", "json"]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            command=command,
            args=list(args),
            ok=False,
            error={
                "code": "timeout",
                "message": f"REA command exceeded {timeout}s",
                "retryable": True,
            },
            duration_s=time.monotonic() - start,
        )
    except OSError as e:
        return ProbeResult(
            command=command,
            args=list(args),
            ok=False,
            error={"code": "rea_not_runnable", "message": str(e)},
            duration_s=time.monotonic() - start,
        )
    duration = time.monotonic() - start
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        # REA reports capability failures as JSON on stdout with rc=1.
        try:
            doc = json.loads(out)
        except json.JSONDecodeError:
            doc = {"code": "nonzero_exit", "message": (proc.stderr or out)[:2000]}
        return ProbeResult(
            command=command,
            args=list(args),
            ok=False,
            error=doc if isinstance(doc, dict) else {"code": "unknown", "message": str(doc)[:2000]},
            duration_s=duration,
            raw=out[:4000],
        )
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return ProbeResult(
            command=command,
            args=list(args),
            ok=False,
            error={"code": "unparseable_output", "message": out[:2000]},
            duration_s=duration,
            raw=out[:4000],
        )
    return ProbeResult(
        command=command,
        args=list(args),
        ok=True,
        envelope=doc if isinstance(doc, dict) else {"result": doc},
        duration_s=duration,
    )
