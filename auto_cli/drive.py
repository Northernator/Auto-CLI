"""Live-session driving: attach to a running app, exercise its automation
surface, and record what actually happens.

REA observes but never mutates a target (its own contract). So the division
of labor is:

  auto-cli drives  - attaches to the app's Inspector endpoint and
                          evaluates probe expressions against live channels
  REA witnesses         - observes the runtime and reconciles it against the
                          static evidence, so every upgrade carries proof

Findings that were `unverifiable` after static recon become `live-verified`
with observed responses, and the generated harness's `send` command is
implemented against the same endpoint.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import evidence as ev
from . import rea_client, scaffold, surface

INVENTORY_PROBES = [
    "JSON.stringify((globalThis.channels && channels.list && channels.list()) || null)",
    "JSON.stringify(Object.keys(globalThis).filter(k => /chan|ipc/i.test(k)))",
]
SEND_PROBE_TEMPLATE = (
    "(async () => JSON.stringify((globalThis.channels && channels.send) "
    "? await channels.send({channel}, {payload}) : null))()"
)

INSPECTOR_SEND_MJS = '''// Send one IPC-style channel message through a live V8 Inspector target.
// Dependency-free: Node >= 22 global WebSocket. Usage:
//   node inspector_send.mjs <inspectorEndpoint> <channel> <jsonPayload>
const endpoint = process.argv[2];
const channel = process.argv[3];
const payload = process.argv[4] || "{}";

async function main() {
  const list = await fetch(endpoint.replace(/\\/$/, "") + "/json/list");
  const targets = await list.json();
  const target = targets.find((t) => t.webSocketDebuggerUrl) || targets[0];
  if (!target) {
    console.error("no inspector target found");
    process.exit(3);
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  };
  const send = (method, params) =>
    new Promise((resolve) => {
      const myId = ++id;
      pending.set(myId, resolve);
      ws.send(JSON.stringify({ id: myId, method, params }));
    });
  await new Promise((r) => (ws.onopen = r));
  await send("Runtime.enable", {});
  const expr =
    "(async () => { if (!(globalThis.channels && channels.send)) " +
    "throw new Error('no channels.send in target'); return JSON.stringify(await channels.send(" +
    JSON.stringify(channel) +
    ", " +
    payload +
    ")); })()";
  const res = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  ws.close();
  const value = res.result && res.result.result && res.result.result.value;
  if (value === undefined) {
    console.error(JSON.stringify(res.result && res.result.exceptionDetails || res));
    process.exit(2);
  }
  console.log(value);
}

main().catch((e) => {
  console.error(String(e));
  process.exit(1);
});
'''


@dataclass
class DriveStep:
    name: str
    ok: bool
    detail: str = ""
    response: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DriveReport:
    target: str
    inspector: str = ""
    steps: list[DriveStep] = field(default_factory=list)
    live_channels: dict = field(default_factory=dict)  # channel -> observed response
    limitations: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        ok = sum(1 for s in self.steps if s.ok)
        return {"ok": ok, "failed": len(self.steps) - ok}

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "inspector": self.inspector,
            "counts": self.counts,
            "steps": [s.to_dict() for s in self.steps],
            "live_channels": self.live_channels,
            "limitations": self.limitations,
        }

    def markdown(self) -> str:
        lines = [
            f"# Drive report: {self.target}",
            "",
            "Live-session driving: the forge attached to the running app,",
            "exercised its channels, and REA witnessed the runtime.",
            "",
            f"Inspector endpoint: `{self.inspector or 'n/a'}`",
            "",
            "| step | ok | detail |",
            "| --- | --- | --- |",
        ]
        for s in self.steps:
            lines.append(f"| {s.name} | {'yes' if s.ok else 'NO'} | {s.detail[:80]} |")
        if self.live_channels:
            lines += ["", "## Observed channel responses", ""]
            for ch, resp in self.live_channels.items():
                lines.append(f"- `{ch}` -> `{resp[:120]}`")
        if self.limitations:
            lines += ["", "## Limitations", ""]
            lines += [f"- {l}" for l in self.limitations]
        lines.append("")
        return "\n".join(lines)


def wait_for_inspector(endpoint: str, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    url = endpoint.rstrip("/") + "/json/list"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def launch(cmd: str, cwd: str | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _find_target_id(envelope: dict) -> str | None:
    """Dig a target id out of a REA target-list envelope.

    Priority matters: `provider.id` also matches a naive "id" search and is
    NOT a target id. Prefer explicit target_id/targetId keys anywhere.
    """
    hits: dict[str, list[str]] = {"target_id": [], "targetId": [], "id": []}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in hits and isinstance(v, str) and len(v) > 8:
                    hits[k].append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(envelope or {})
    for key in ("target_id", "targetId", "id"):
        if hits[key]:
            return hits[key][0]
    return None


def _run_inspector_eval(inspector: str, expression: str, timeout: float = 15.0) -> str:
    """Evaluate one expression in the live target via a throwaway Node CDP client."""
    script = (
        "const ep=process.argv[1],expr=process.argv[2];"
        "(async()=>{const l=await (await fetch(ep.replace(/\\/$/,'')+'/json/list')).json();"
        "const t=l.find(x=>x.webSocketDebuggerUrl)||l[0];"
        "const ws=new WebSocket(t.webSocketDebuggerUrl);let id=0;const p=new Map();"
        "ws.onmessage=m=>{const d=JSON.parse(m.data);if(d.id&&p.has(d.id)){p.get(d.id)(d);p.delete(d.id);}};"
        "const send=(method,params)=>new Promise(r=>{const i=++id;p.set(i,r);ws.send(JSON.stringify({id:i,method,params}));});"
        "await new Promise(r=>ws.onopen=r);await send('Runtime.enable',{});"
        "const res=await send('Runtime.evaluate',{expression:expr,returnByValue:true,awaitPromise:true});"
        "ws.close();const v=res.result&&res.result.result&&res.result.result.value;"
        "console.log(v===undefined?'__ERROR__':v);})().catch(e=>{console.error(String(e));process.exit(1);});"
    )
    proc = subprocess.run(
        ["node", "-e", script, inspector, expression],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode:
        raise OSError((proc.stderr or proc.stdout or "inspector evaluation failed").strip()[:200])
    return (proc.stdout or "").strip()


def drive(
    bundle: ev.EvidenceBundle,
    plan: surface.HarnessPlan,
    scaffold_dir: str | Path,
    inspector: str,
    channels: list[str] | None = None,
    observation_ms: int = 1500,
    rea: rea_client.ReaLocation | None = None,
    timeout: int = 120,
) -> DriveReport:
    """Attach to the live runtime, exercise the discovered channels, witness
    with REA, and upgrade the scaffold."""
    report = DriveReport(target=plan.target, inspector=inspector)
    report.limitations.append(
        "REA never mutates the target - the forge evaluates probes itself and REA witnesses"
    )

    targets = [c for c in (channels or [f.value for f in bundle.of_kind("ipc_channel")])]

    # 1. REA witnesses the live runtime.
    if rea:
        listed = rea_client.run_probe(rea, "list-javascript-runtime-targets", [inspector], timeout=timeout)
        report.steps.append(
            DriveStep(
                name="rea list-javascript-runtime-targets",
                ok=listed.ok,
                detail="runtime targets listed" if listed.ok else str(listed.error)[:120],
            )
        )
        tid = _find_target_id(listed.envelope) if listed.ok else None
        if tid:
            obs = rea_client.run_probe(
                rea,
                "observe-javascript-runtime",
                [inspector, tid, "--runtime-kind", "node", "--observation-ms", str(observation_ms)],
                timeout=timeout,
            )
            report.steps.append(
                DriveStep(
                    name="rea observe-javascript-runtime",
                    ok=obs.ok,
                    detail=f"observed {observation_ms}ms" if obs.ok else str(obs.error)[:120],
                )
            )
            if obs.ok and obs.envelope:
                # runtime evidence joins the bundle with live provenance
                bundle.probes.append(
                    ev.ProbeRecord(command="observe-javascript-runtime", args=[inspector, tid], ok=True)
                )
                bundle.findings.extend(ev.extract_findings("observe-javascript-runtime", obs.envelope))
                bundle.findings = ev._dedupe(bundle.findings)
        else:
            report.limitations.append("no inspector target id resolved - REA witness skipped")

    # 2. Inventory probe: what does the live target actually expose?
    inv = None
    for probe in INVENTORY_PROBES:
        try:
            out = _run_inspector_eval(inspector, probe)
        except (subprocess.TimeoutExpired, OSError) as e:
            report.steps.append(DriveStep(name="inventory probe", ok=False, detail=str(e)[:120]))
            out = None
            break
        if out and out != "__ERROR__" and out != "null":
            inv = out
            break
    if inv:
        report.steps.append(DriveStep(name="inventory probe", ok=True, detail=f"live surface: {inv[:100]}"))
        try:
            live = json.loads(inv)
            if isinstance(live, list):
                targets = [t for t in targets if t in live] + [t for t in live if t not in targets]
        except json.JSONDecodeError:
            pass
    else:
        report.steps.append(
            DriveStep(name="inventory probe", ok=False, detail="no channel inventory exposed by target")
        )
        report.limitations.append(
            "target exposes no discoverable channel inventory - send probes only cover recon channels"
        )

    # 3. Drive each channel and record the real response.
    for ch in targets:
        expr = SEND_PROBE_TEMPLATE.format(channel=json.dumps(ch), payload="{}")
        try:
            out = _run_inspector_eval(inspector, expr)
        except (subprocess.TimeoutExpired, OSError) as e:
            report.steps.append(DriveStep(name=f"send {ch}", ok=False, detail=str(e)[:120]))
            continue
        if out and out not in ("__ERROR__", "null", "undefined"):
            report.live_channels[ch] = out
            report.steps.append(DriveStep(name=f"send {ch}", ok=True, response=out, detail=f"responded: {out[:80]}"))
        else:
            report.steps.append(DriveStep(name=f"send {ch}", ok=False, detail="no response / channel absent"))

    # 4. Upgrade the scaffold: real send implementation + tests + skill.
    if report.live_channels:
        _upgrade_scaffold(Path(scaffold_dir), plan, report)

    return report


def _upgrade_scaffold(root: Path, plan: surface.HarnessPlan, report: DriveReport) -> None:
    if not report.live_channels:
        return  # nothing confirmed live -> the scaffold stays honest
    pkg = next((p for p in root.glob("cli_anything/*") if (p / "core/backend.py").is_file()), None)
    if not pkg:
        return
    module = pkg.name
    live = sorted(report.live_channels)

    # 4a. backend: LIVE_CHANNELS constant + inspector endpoint
    backend = pkg / "core" / "backend.py"
    src = backend.read_text(encoding="utf-8")
    src = src.replace(
        "ALLOWED_ORIGINS = ",
        f"INSPECTOR_ENDPOINT = {report.inspector!r}\nLIVE_CHANNELS = {live!r}\nALLOWED_ORIGINS = ",
        1,
    )
    backend.write_text(src, encoding="utf-8")

    # 4b. utils/inspector_send.mjs - the transport, dependency-free
    utils = pkg / "utils"
    utils.mkdir(parents=True, exist_ok=True)
    (utils / "inspector_send.mjs").write_text(INSPECTOR_SEND_MJS, encoding="utf-8")

    # 4c. CLI: real send command (plain string - no f-string, braces are literal)
    cli_file = pkg / f"{module}_cli.py"
    src = cli_file.read_text(encoding="utf-8")
    send_impl = '''@main.command()
@click.argument("channel")
@click.option("--message", default="{}", help="JSON payload")
@click.pass_context
def send(ctx: click.Context, channel: str, message: str) -> None:
    """Send one channel message through the live Inspector endpoint.

    Implemented after live driving: these channels were exercised against the
    running app and responded. See DRIVE-REPORT.md for observed responses.
    """
    import shutil as _shutil
    import subprocess as _sp
    from pathlib import Path as _Path

    if channel not in backend.LIVE_CHANNELS:
        _fail("channel not confirmed live: %s (confirmed: %s)" % (channel, backend.LIVE_CHANNELS))
    if not _shutil.which("node"):
        _fail("node is required to speak to the Inspector endpoint")
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        _fail("--message must be valid JSON")
    script = _Path(__file__).parent / "utils" / "inspector_send.mjs"
    try:
        proc = _sp.run(
            ["node", str(script), backend.INSPECTOR_ENDPOINT, channel, json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (_sp.TimeoutExpired, OSError) as e:
        _fail("inspector send failed: %s" % e)
    if proc.returncode != 0:
        _fail("inspector send failed: %s" % (proc.stderr or proc.stdout)[:200])
    _emit(ctx, {"channel": channel, "response": proc.stdout.strip()})
'''
    src = re.sub(
        r'@main\.command\(\)\n@click\.argument\("channel"\)\n@click\.option\("--message".*?_fail\("send is not wired yet[^"]*"\)\n',
        lambda _m: send_impl,
        src,
        flags=re.S,
    )
    cli_file.write_text(src, encoding="utf-8")

    # 4d. tests: allowlist + payload validation, no live target needed
    tests = pkg / "tests" / "test_core.py"
    src = tests.read_text(encoding="utf-8")
    src += f'''

def test_live_channels_recorded():
    from cli_anything.{module}.core import backend

    assert backend.LIVE_CHANNELS == {live!r}
    assert backend.INSPECTOR_ENDPOINT == {report.inspector!r}


def test_send_rejects_unconfirmed_channel():
    """A channel that never responded live must not be sendable."""
    from click.testing import CliRunner
    from cli_anything.{module} import {module}_cli

    res = CliRunner().invoke({module}_cli.main, ["send", "not:a-channel"])
    assert res.exit_code == 2
'''
    tests.write_text(src, encoding="utf-8")

    # 4e. SKILL.md: observed responses section
    skill = pkg / "skills" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    observed = "\n".join(f"- `{ch}` -> `{resp[:120]}`" for ch, resp in sorted(report.live_channels.items()))
    cli_name = "cli-anything-" + re.sub(r"[^a-z0-9]+", "-", module.replace("_", "-")).strip("-")
    text = text.replace(
        "## Rules for agents",
        "## Driven live (see DRIVE-REPORT.md)\n\n"
        "These channels were exercised against the running app over the Inspector:\n\n"
        + observed
        + "\n\n```bash\n"
        + cli_name
        + " send <channel> --message '{}'\n```\n\n## Rules for agents",
    )
    skill.write_text(text, encoding="utf-8")


def write_report(report: DriveReport, scaffold_dir: str | Path) -> Path:
    root = Path(scaffold_dir)
    if root.name != "agent-harness":
        found = next(root.rglob("agent-harness"), None)
        if found:
            root = found
    p = root / "DRIVE-REPORT.md"
    p.write_text(report.markdown(), encoding="utf-8")
    (root / "drive.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return p
