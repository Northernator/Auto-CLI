"""auto-cli CLI: REA recon in, CLI-Anything harness out.

    auto-cli doctor
    auto-cli probe     --target PATH [--out DIR]
    auto-cli probe     --url URL [--browser PATH] [--cdp ENDPOINT] [--out DIR]
    auto-cli surface   --bundle FILE
    auto-cli scaffold  --bundle FILE --name NAME [--out DIR]
    auto-cli pipeline  --target PATH --name NAME [--out DIR]
    auto-cli pipeline  --url URL --name NAME [--browser PATH] [--out DIR]
    auto-cli validate  --dir PATH
    auto-cli verify    --bundle FILE --dir DIR     # prove findings live
    auto-cli drive     --bundle FILE --dir DIR --inspector URL   # drive live channels
    auto-cli complete  --bundle FILE --dir DIR     # generate domain surface
    auto-cli deep      --bundle FILE --dir DIR --agent codex|claude
    auto-cli auto      --target PATH [--launch CMD] [--inspector URL] [--name NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import evidence as ev
from . import (
    complete as cmp_mod,
    deep,
    drive as drv,
    gui as gui_mod,
    rea_client,
    scaffold,
    surface,
    validate as vmod,
    verify as vfy,
)

# REA probes the forge runs by default for a filesystem target, in order.
DEFAULT_PROBES: list[tuple[str, list[str]]] = [
    ("analyze", ["{target}"]),
]

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_browser() -> str | None:
    env = os.environ.get("HARNESS_FORGE_BROWSER")
    if env and Path(env).exists():
        return env
    for c in BROWSER_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def build_scenario(url: str, browser: str, wait_ms: int) -> dict:
    """Minimal REA browser scenario: navigate, settle, capture DOM + URL.

    Network events with response bodies are retained so export-web-scripts
    produces real .js files for the analyze-javascript-application leg.
    """
    return {
        "browser": {"mode": "launch", "executable_path": browser},
        "start_url": {"url": url},
        "actions": [
            {"action": "wait_for_timeout", "step_id": "settle", "duration_ms": wait_ms}
        ],
        "capture": {
            "at_end": ["dom", "url"],
            "events": ["network"],
            "network": {"response_body": True},
        },
    }


def _looks_like_js_app(target: Path) -> bool:
    if target.is_file():
        return target.suffix.lower() in {".asar", ".js", ".mjs", ".cjs"}
    if target.suffix.lower() == ".asar":
        return True
    return next(target.rglob("*.js"), None) is not None


def _target_probes(target: Path, only: list[str] | None) -> list[tuple[str, list[str]]]:
    probes = []
    for cmd, args in DEFAULT_PROBES:
        if only and cmd not in only:
            continue
        probes.append((cmd, [a.format(target=str(target)) for a in args]))
    if (not only or "analyze-javascript-application" in only) and _looks_like_js_app(target):
        probes.append(("analyze-javascript-application", [str(target)]))
    if not only or "inspect-sqlite-database" in only:
        databases = (
            [target] if target.is_file() and target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
            else sorted(p for p in target.rglob("*") if p.suffix.lower() in {".db", ".sqlite", ".sqlite3"})
        )
        for db in databases:
            probes.append(("inspect-sqlite-database", [str(db), "--schema-scope", "database"]))
            break
    for cmd in only or []:
        if cmd not in {c for c, _ in probes}:
            probes.append((cmd, [str(target)]))
    return probes


def _find_target_id(envelope: dict, origin: str) -> str | None:
    """Dig a CDP target id out of list-browser-targets output, best effort.

    Priority matters: provider.id also matches a naive "id" search and is not
    a target id.
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


def run_url_chain(
    loc: rea_client.ReaLocation,
    url: str,
    out: Path,
    browser: str | None,
    wait_ms: int,
    cdp: str | None,
    timeout: int,
) -> tuple[list, str]:
    """Website recon chain: capture -> export scripts -> analyze -> (optional) CDP."""
    results = []

    if not browser:
        browser = find_browser()
    if not browser:
        results.append(
            rea_client.ProbeResult(
                command="capture-browser-scenario",
                args=[url],
                ok=False,
                error={
                    "code": "no_browser",
                    "message": "no Chrome-family browser found; pass --browser PATH",
                },
            )
        )
        return results, url

    out.mkdir(parents=True, exist_ok=True)
    scenario_path = (out / "scenario.json").resolve()
    scenario_path.write_text(
        json.dumps(build_scenario(url, browser, wait_ms), indent=2), encoding="utf-8"
    )
    print("  probing: rea capture-browser-scenario...", flush=True)
    cap = rea_client.run_probe(loc, "capture-browser-scenario", [str(scenario_path)], timeout=timeout)
    results.append(cap)
    if not cap.ok:
        return results, url

    capture_file = (out / "web-capture.json").resolve()
    capture_file.write_text(json.dumps(cap.envelope, indent=2), encoding="utf-8")

    scripts_dir = (out / "web-scripts").resolve()
    if scripts_dir.exists():
        shutil.rmtree(scripts_dir)
    print("  probing: rea export-web-scripts...", flush=True)
    exp = rea_client.run_probe(
        loc, "export-web-scripts", [str(capture_file), str(scripts_dir)], timeout=timeout
    )
    results.append(exp)

    if scripts_dir.exists() and any(scripts_dir.rglob("*.js")):
        print("  probing: rea analyze-javascript-application (exported scripts)...", flush=True)
        results.append(
            rea_client.run_probe(
                loc, "analyze-javascript-application", [str(scripts_dir)], timeout=timeout
            )
        )

    if cdp:
        print(f"  probing: rea list-browser-targets {cdp}...", flush=True)
        targets = rea_client.run_probe(loc, "list-browser-targets", [cdp], timeout=timeout)
        results.append(targets)
        tid = _find_target_id(targets.envelope, url) if targets.ok else None
        if tid:
            print(f"  probing: rea discover-webmcp-tools (target {tid[:12]}...)...", flush=True)
            results.append(
                rea_client.run_probe(
                    loc, "discover-webmcp-tools", [cdp, tid, "--allowed-origins", url], timeout=timeout
                )
            )
            results.append(
                rea_client.run_probe(loc, "analyze-web-bundle", [cdp, tid], timeout=timeout)
            )
        else:
            results.append(
                rea_client.ProbeResult(
                    command="discover-webmcp-tools",
                    args=[cdp],
                    ok=False,
                    error={"code": "no_target", "message": "no CDP target id resolved"},
                )
            )

    return results, url


def cmd_doctor(args) -> int:
    loc = rea_client.find_rea()
    if not loc:
        print("REA not found. Install: npm install -g rea-agents  (or set REA_BIN)")
        return 1
    try:
        ver = rea_client.version(loc)
    except Exception as e:  # noqa: BLE001
        print(f"REA found ({loc.detail}) but --version failed: {e}")
        return 1
    print(f"REA ok: {ver} ({loc.kind}: {loc.detail})")
    return 0


def cmd_probe(args) -> int:
    loc = rea_client.find_rea()
    if not loc:
        print("REA not found. Install: npm install -g rea-agents", file=sys.stderr)
        return 1
    if not args.target and not args.url:
        print("give me --target PATH or --url URL", file=sys.stderr)
        return 1

    if getattr(args, "url", None):
        target = args.url
        if not Path(target).exists() and not target.startswith(("http://", "https://")):
            print(f"target not found: {target}", file=sys.stderr)
            return 1
        results, target = run_url_chain(
            loc, target, Path(args.out), args.browser, args.wait_ms, args.cdp, args.timeout
        )
        fs = []
    else:
        target = Path(args.target)
        if not target.exists():
            print(f"target not found: {target}", file=sys.stderr)
            return 1
        target = target.resolve()
        probes = _target_probes(target, args.probe)
        results = []
        for cmd, cargs in probes:
            print(f"  probing: rea {cmd} {' '.join(cargs[:2])}...", flush=True)
            results.append(rea_client.run_probe(loc, cmd, cargs, timeout=args.timeout))
        fs = ev.local_filesystem_probe(target)
    bundle = ev.bundle_from_probes(str(target), results, fs)
    out = Path(args.out) / "evidence-bundle.json"
    bundle.save(out)
    ok = sum(1 for p in bundle.probes if p.ok)
    print(f"probes: {ok}/{len(bundle.probes)} ok, findings: {len(bundle.findings)}")
    print(f"bundle: {out}")
    return 0


def _load_bundle(path: str) -> ev.EvidenceBundle:
    return ev.EvidenceBundle.load(path)


def cmd_surface(args) -> int:
    bundle = _load_bundle(args.bundle)
    plan = surface.classify(bundle)
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, default=str))
    else:
        print("\n".join(plan.summary_lines()))
    return 0


def cmd_scaffold(args) -> int:
    bundle = _load_bundle(args.bundle)
    plan = surface.classify(bundle)
    root_override = None
    if plan.target.startswith(("http://", "https://")):
        root_override = Path(args.out) / "web-scripts"
    root = scaffold.generate(
        plan,
        bundle,
        args.out,
        name=args.name,
        author=args.author,
        repl_skin=args.repl_skin,
        target_root=root_override,
    )
    print(f"scaffold: {root}")
    return 0


def cmd_pipeline(args) -> int:
    loc = rea_client.find_rea()
    if not loc:
        print("REA not found. Install: npm install -g rea-agents", file=sys.stderr)
        return 1
    if not args.target and not args.url:
        print("give me --target PATH or --url URL", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if getattr(args, "url", None):
        url = args.url
        if not url.startswith(("http://", "https://")):
            print(f"--url must be an http(s) URL: {url}", file=sys.stderr)
            return 1
        print(f"[1/4] website recon: {url}...", flush=True)
        results, target = run_url_chain(
            loc, url, out, args.browser, args.wait_ms, args.cdp, args.timeout
        )
        fs = []
    else:
        target_path = Path(args.target)
        if not target_path.exists():
            print(f"target not found: {target_path}", file=sys.stderr)
            return 1
        target_path = target_path.resolve()
        probes = _target_probes(target_path, args.probe)
        results = []
        for cmd, cargs in probes:
            print(f"[1/4] probing: rea {cmd}...", flush=True)
            results.append(rea_client.run_probe(loc, cmd, cargs, timeout=args.timeout))
        fs = ev.local_filesystem_probe(target_path)
        target = str(target_path)

    bundle = ev.bundle_from_probes(target, results, fs)
    bundle.save(out / "evidence-bundle.json")

    print("[2/4] classifying automation surface...", flush=True)
    plan = surface.classify(bundle)
    print("\n".join("      " + l for l in plan.summary_lines()))

    print("[3/4] generating harness scaffold...", flush=True)
    root_override = out / "web-scripts" if target.startswith(("http://", "https://")) else None
    root = scaffold.generate(
        plan,
        bundle,
        out,
        name=args.name,
        author=args.author,
        repl_skin=args.repl_skin,
        target_root=root_override,
    )
    print(f"      scaffold: {root}")

    print("[4/4] validating...", flush=True)
    report = vmod.validate(root)
    for line in report.lines():
        print("      " + line)
    return 0 if report.ok else 2


def cmd_complete(args) -> int:
    """Generate the domain surface (table groups, endpoint commands) from
    verified evidence."""
    bundle = _load_bundle(args.bundle)
    plan = surface.classify(bundle)
    verification = None
    vfile = Path(args.dir) / "verification.json"
    if not vfile.exists():
        vfile = next(Path(args.dir).rglob("verification.json"), None)
    if vfile and vfile.exists():
        verification = json.loads(vfile.read_text(encoding="utf-8"))
    report = cmp_mod.complete(bundle, plan, args.dir, verification)
    print("\n".join(report.lines()))
    return 0


def cmd_auto(args) -> int:
    """Fully automatic: recon -> scaffold -> verify -> drive -> complete ->
    validate. Launches the app itself when --launch is given."""
    if not args.agent and (args.agent_prepare_only or args.agent_task or args.agent_timeout != 900):
        print("agent options require --agent codex|claude", file=sys.stderr)
        return 2
    if args.agent_timeout <= 0:
        print("agent timeout must be greater than zero", file=sys.stderr)
        return 2
    loc = rea_client.find_rea()
    if not loc:
        print("REA not found. Install: npm install -g rea-agents", file=sys.stderr)
        return 1
    if not args.target and not args.url:
        print("give me --target PATH or --url URL", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. recon + scaffold
    if args.url:
        url = args.url
        if not url.startswith(("http://", "https://")):
            print(f"--url must be an http(s) URL: {url}", file=sys.stderr)
            return 1
        print(f"[1/5] website recon: {url}...", flush=True)
        results, target = run_url_chain(
            loc, url, out, args.browser, args.wait_ms, args.cdp, args.timeout
        )
        fs = []
    else:
        target_path = Path(args.target).resolve()
        if not target_path.exists():
            print(f"target not found: {target_path}", file=sys.stderr)
            return 1
        print(f"[1/5] recon: {target_path}...", flush=True)
        probes = _target_probes(target_path, args.probe)
        results = []
        for cmd, cargs in probes:
            print(f"      rea {cmd}...", flush=True)
            results.append(rea_client.run_probe(loc, cmd, cargs, timeout=args.timeout))
        fs = ev.local_filesystem_probe(target_path)
        target = str(target_path)

    bundle = ev.bundle_from_probes(target, results, fs)
    bundle.save(out / "evidence-bundle.json")
    plan = surface.classify(bundle)
    print(f"      flavor: {plan.primary_flavor} ({', '.join(plan.flavors)})")

    print("[2/5] scaffold...", flush=True)
    root_override = out / "web-scripts" if target.startswith(("http://", "https://")) else None
    root = scaffold.generate(
        plan, bundle, out, name=args.name, author=args.author,
        repl_skin=args.repl_skin, target_root=root_override,
    )
    print(f"      {root}")

    # 2. launch + verify + drive
    proc = None
    inspector = args.inspector
    if args.launch:
        print(f"[3/5] launching app: {args.launch}...", flush=True)
        proc = drv.launch(args.launch, cwd=args.cwd)
        if not inspector:
            m = re.search(r"--inspect(?:-brk)?[= ](\d+)", args.launch)
            inspector = f"http://127.0.0.1:{m.group(1)}" if m else None
    else:
        print("[3/5] no --launch: static verify only...", flush=True)

    verification = None
    try:
        if inspector:
            if not drv.wait_for_inspector(inspector, timeout_s=args.wait):
                print("      inspector never came up", file=sys.stderr)
            else:
                vrep = vfy.verify(bundle, plan, root, timeout=5.0)
                vfy.write_report(vrep, root)
                verification = vrep.to_dict()
                print(f"      verify: {vrep.counts}")
                drep = drv.drive(
                    bundle, plan, root, inspector,
                    observation_ms=args.observe_ms, rea=loc, timeout=args.timeout,
                )
                drv.write_report(drep, root)
                print(f"      drive: {sorted(drep.live_channels)} confirmed live")
        else:
            vrep = vfy.verify(bundle, plan, root, timeout=5.0)
            vfy.write_report(vrep, root)
            verification = vrep.to_dict()
            print(f"      verify: {vrep.counts}")
    finally:
        if proc:
            proc.terminate()

    # 3. complete: generate the domain surface from verified evidence
    print("[4/5] completing domain surface...", flush=True)
    creport = cmp_mod.complete(bundle, plan, root, verification)
    for line in creport.lines():
        print("      " + line)

    # 3b. GUI leg: drive the UIA surface and add the ui command group
    gui_failed = False
    if args.gui_process or args.gui_pid or args.gui_title:
        print("[4b/5] GUI leg: driving UIA...", flush=True)
        try:
            window = gui_mod.find_window(
                pid=args.gui_pid, process=args.gui_process, title_contains=args.gui_title
            )
            if window:
                grep = gui_mod.GuiReport(
                    process=window.ClassName or "",
                    pid=window.ProcessId,
                    window=window.Name or "",
                )
                grep.nodes = gui_mod.capture_tree(window)
                gui_mod.write_report(grep, root)
                menus = gui_mod.menu_paths(grep.nodes)
                print(f"      window: {grep.window} | {len(grep.nodes)} nodes | {len(menus)} menu paths")
                if gui_mod.upgrade_scaffold(root, grep):
                    print("      scaffold upgraded: ui command group added")
            else:
                print("      GUI window not found", file=sys.stderr)
                gui_failed = True
        except gui_mod.GuiError as e:
            print(f"      GUI leg skipped: {e}", file=sys.stderr)
            gui_failed = True

    # 4. validate (one repair pass if tests fail)
    print("[5/5] validate...", flush=True)
    report = vmod.validate(root)
    for line in report.lines():
        print("      " + line)
    if not report.ok and args.repair:
        print("      repair: regenerating and re-validating...", flush=True)
        cmp_mod.complete(bundle, plan, root, verification)
        report = vmod.validate(root)
        for line in report.lines():
            print("      " + line)
    ok = report.ok and not gui_failed
    if args.agent and ok:
        print(f"[deep] handing verified findings to {args.agent}...", flush=True)
        try:
            deep_report = deep.generate(
                bundle, root, agent=args.agent, task=args.agent_task,
                timeout=args.agent_timeout, prepare_only=args.agent_prepare_only,
            )
            print(f"      deep: {deep_report.status} ({deep_report.run_dir})")
            if deep_report.error:
                print(f"      {deep_report.error}", file=sys.stderr)
            ok = deep_report.ok
        except (OSError, ValueError) as exc:
            print(f"deep: {exc}", file=sys.stderr)
            ok = False
    print(f"result: {'OK' if ok else 'FAILED'}")
    print(f"harness: {root}")
    return 0 if ok else 2


def cmd_deep(args) -> int:
    try:
        report = deep.generate(
            _load_bundle(args.bundle), args.dir, agent=args.agent, task=args.task,
            timeout=args.timeout, prepare_only=args.prepare_only,
        )
    except (OSError, ValueError) as exc:
        print(f"deep: {exc}", file=sys.stderr)
        return 2
    print(f"deep: {report.status}")
    print(f"artifacts: {report.run_dir}")
    if report.error:
        print(report.error, file=sys.stderr)
    return 0 if report.ok else 2


def cmd_gui(args) -> int:
    """Windows GUI driving.

    Prefers REA's native-windows provider (the rea-windows repo) so GUI
    evidence is a real REA evidence envelope; falls back to the built-in UIA
    driver when the provider is unavailable. --backend rea|local forces one.
    """
    backend = getattr(args, "backend", "auto") or "auto"
    rea = rea_client.find_rea() if backend != "local" else None
    use_rea = False
    window_id = args.window_id
    pid = args.pid or 0

    if backend == "local":
        use_rea = False
    elif not rea:
        if backend == "rea":
            print("REA not found. Install: npm install -g rea-agents", file=sys.stderr)
            return 1
        print("[gui] REA not found - using the built-in UIA driver", file=sys.stderr)
    else:
        found = _gui_find_window(args)
        if window_id is None:
            if found is None:
                print(
                    "window not found. Give --pid, --process <ClassName>, or --title <substr>",
                    file=sys.stderr,
                )
                return 1
            window_id = found.NativeWindowHandle
        if not pid:
            # REA needs the owning pid to resolve the executable target
            by_handle = gui_mod.find_window_by_handle(window_id)
            if by_handle is not None:
                pid = by_handle.ProcessId
            elif found is not None:
                pid = found.ProcessId
        use_rea = True if backend == "rea" else gui_mod.probe_rea_gui(rea, window_id, pid)

    if use_rea and rea and window_id is not None:
        print(f"[gui] backend: REA native-windows provider (window {window_id})")
        return _cmd_gui_via_rea(args, rea, window_id, pid)

    print("[gui] backend: built-in UIA driver")
    return _cmd_gui_local(args)


def _gui_find_window(args):
    try:
        if args.window_id is not None:
            return gui_mod.find_window_by_handle(args.window_id)
        return gui_mod.find_window(
            pid=args.pid, process=args.process, title_contains=args.title
        )
    except gui_mod.GuiError as e:
        print(str(e), file=sys.stderr)
        return None


def _cmd_gui_local(args) -> int:
    try:
        window = (
            gui_mod.find_window_by_handle(args.window_id)
            if args.window_id is not None else
            gui_mod.find_window(pid=args.pid, process=args.process, title_contains=args.title)
        )
    except gui_mod.GuiError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not window:
        print(
            "window not found. Give --pid, --process <ClassName>, or --title <substr>",
            file=sys.stderr,
        )
        return 1

    report = gui_mod.GuiReport(
        process=window.ClassName or "", pid=window.ProcessId, window=window.Name or ""
    )
    report.nodes = gui_mod.capture_tree(window, maxdepth=args.depth)
    print(f"window: {report.window} (pid {report.pid}, {len(report.nodes)} nodes)")
    menus = gui_mod.menu_paths(report.nodes)
    print(f"menu paths: {len(menus)}")
    for m in menus[:15]:
        print(f"  {m}")

    if args.tree:
        for n in report.nodes:
            if n.patterns:
                print(f"  [{n.control_type}] {n.path} {{{','.join(n.patterns)}}}")

    shot_dir = Path(args.out)
    shot_dir.mkdir(parents=True, exist_ok=True)

    for spec in args.invoke or []:
        r = gui_mod.act(window, "invoke", spec, yes=args.yes, allow_destructive=args.allow_destructive, shot_dir=shot_dir)
        report.actions.append(r)
        print(f"[{'ok  ' if r.ok else 'FAIL'}] invoke {spec} - {r.detail[:60]}")

    for spec in args.select or []:
        r = gui_mod.act(window, "select", spec, yes=args.yes, allow_destructive=args.allow_destructive, shot_dir=shot_dir)
        report.actions.append(r)
        print(f"[{'ok  ' if r.ok else 'FAIL'}] select {spec} - {r.detail[:60]}")

    for spec in args.set_text or []:
        path, _, value = spec.partition("=")
        r = gui_mod.act(window, "set-value", path, value, yes=args.yes, allow_destructive=args.allow_destructive, shot_dir=shot_dir)
        report.actions.append(r)
        print(f"[{'ok  ' if r.ok else 'FAIL'}] set-value {path} - {r.detail[:60]}")

    out = gui_mod.write_report(report, args.dir or args.out)
    print(f"report: {out}")
    if args.dir and gui_mod.upgrade_scaffold(Path(args.dir), report):
        print("scaffold upgraded: ui command group added")
    return 0 if all(action.ok for action in report.actions) else 2


def _cmd_gui_via_rea(args, rea, window_id: int, pid: int) -> int:
    """Drive the GUI through REA's native-windows provider."""
    try:
        nodes = gui_mod.capture_via_rea(rea, window_id, pid, max_nodes=args.depth * 80)
    except gui_mod.GuiError as e:
        if args.backend == "rea":
            print(f"[gui] REA capture failed: {e}", file=sys.stderr)
            return 1
        print(
            f"[gui] REA capture failed ({e}) - falling back to the built-in driver",
            file=sys.stderr,
        )
        return _cmd_gui_local(args)

    window_name = nodes[0].path.split(" > ")[0] if nodes else "window"
    report = gui_mod.GuiReport(process=args.process or "", pid=pid, window=window_name)
    report.nodes = nodes
    report.limitations.append("captured through REA's native-windows provider (rea-windows)")
    print(f"window: {window_name} (pid {pid}, {len(report.nodes)} nodes)")
    menus = gui_mod.menu_paths(nodes)
    print(f"menu paths: {len(menus)}")
    for m in menus[:15]:
        print(f"  {m}")

    if args.tree:
        for n in nodes:
            if n.patterns:
                print(f"  [{n.control_type}] {n.path} {{{','.join(n.patterns)}}}")

    shot_dir = Path(args.out)
    shot_dir.mkdir(parents=True, exist_ok=True)

    for spec in args.invoke or []:
        r = gui_mod.act_via_rea(
            rea, window_id, "invoke", spec, pid=pid, yes=args.yes,
            allow_destructive=args.allow_destructive, shot_dir=shot_dir, nodes=nodes,
        )
        report.actions.append(r)
        print(f"[{'ok  ' if r.ok else 'FAIL'}] invoke {spec} - {r.detail[:60]}")

    for spec in args.select or []:
        r = gui_mod.act_via_rea(
            rea, window_id, "select", spec, pid=pid, yes=args.yes,
            allow_destructive=args.allow_destructive, shot_dir=shot_dir, nodes=nodes,
        )
        report.actions.append(r)
        print(f"[{'ok  ' if r.ok else 'FAIL'}] select {spec} - {r.detail[:60]}")

    for spec in args.set_text or []:
        path, _, value = spec.partition("=")
        r = gui_mod.act_via_rea(
            rea, window_id, "set-value", path, value, pid=pid, yes=args.yes,
            allow_destructive=args.allow_destructive, shot_dir=shot_dir, nodes=nodes,
        )
        report.actions.append(r)
        print(f"[{'ok  ' if r.ok else 'FAIL'}] set-value {path} - {r.detail[:60]}")

    out = gui_mod.write_report(report, args.dir or args.out)
    print(f"report: {out}")
    if args.dir and gui_mod.upgrade_scaffold(Path(args.dir), report):
        print("scaffold upgraded: ui command group added")
    return 0 if all(action.ok for action in report.actions) else 2


def cmd_validate(args) -> int:
    report = vmod.validate(args.dir)
    print("\n".join(report.lines()))
    return 0 if report.ok else 2


def cmd_verify(args) -> int:
    """Prove recon findings against the live target, patch the scaffold."""
    bundle = _load_bundle(args.bundle)
    plan = surface.classify(bundle)
    report = vfy.verify(bundle, plan, args.dir, timeout=args.timeout)
    out = vfy.write_report(report, args.dir)
    for c in report.checks:
        print(f"[{c.state:<13}] {c.kind}: {c.target[:60]} - {c.detail[:70]}")
    print(f"summary: {report.counts}")
    print(f"report: {out}")
    if report.patches:
        print(f"patched constants: {list(report.patches)}")
    return 0


def cmd_drive(args) -> int:
    """Attach to the running app, exercise its channels, upgrade the scaffold."""
    loc = rea_client.find_rea()
    if not loc:
        print("REA not found. Install: npm install -g rea-agents", file=sys.stderr)
        return 1
    bundle = _load_bundle(args.bundle)
    plan = surface.classify(bundle)

    proc = None
    inspector = args.inspector
    if args.launch:
        print(f"[drive] launching: {args.launch}", flush=True)
        proc = drv.launch(args.launch, cwd=args.cwd)
        if not inspector:
            m = re.search(r"--inspect(?:-brk)?[= ](\d+)", args.launch)
            inspector = f"http://127.0.0.1:{m.group(1)}" if m else None
    if not inspector:
        print("no inspector endpoint: pass --inspector or --launch with --inspect=PORT", file=sys.stderr)
        if proc:
            proc.terminate()
        return 1

    print(f"[drive] waiting for inspector {inspector}...", flush=True)
    if not drv.wait_for_inspector(inspector, timeout_s=args.wait):
        print("inspector never came up - is the app running?", file=sys.stderr)
        if proc:
            proc.terminate()
        return 1

    try:
        report = drv.drive(
            bundle,
            plan,
            args.dir,
            inspector,
            channels=args.channel,
            observation_ms=args.observe_ms,
            rea=loc,
            timeout=args.timeout,
        )
    finally:
        if proc:
            proc.terminate()

    out = drv.write_report(report, args.dir)
    for s in report.steps:
        mark = "ok  " if s.ok else "FAIL"
        print(f"[{mark}] {s.name} - {s.detail[:70]}")
    print(f"live channels: {sorted(report.live_channels)}")
    print(f"report: {out}")
    if report.live_channels:
        print("[drive] scaffold upgraded: send implemented, tests + skill updated")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="auto-cli",
        description="REA recon in, CLI-Anything harness out.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check REA availability")

    sp = sub.add_parser("probe", help="run REA probes against a target")
    sp.add_argument("--target", default=None, help="filesystem target: app dir, .app, .asar, or binary")
    sp.add_argument("--url", default=None, help="website URL (launches a browser)")
    sp.add_argument("--browser", default=None, help="Chrome-family browser executable (URL mode)")
    sp.add_argument("--cdp", default=None, help="loopback CDP endpoint of a running debug browser (URL mode)")
    sp.add_argument("--wait-ms", type=int, default=2000, dest="wait_ms")
    sp.add_argument("--out", default="forge-out")
    sp.add_argument("--probe", action="append", help="restrict to these REA commands")
    sp.add_argument("--timeout", type=int, default=600)

    sp = sub.add_parser("surface", help="classify an evidence bundle")
    sp.add_argument("--bundle", required=True)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("scaffold", help="generate a harness from a bundle")
    sp.add_argument("--bundle", required=True)
    sp.add_argument("--name", default=None)
    sp.add_argument("--out", default="forge-out")
    sp.add_argument("--author", default="auto-cli")
    sp.add_argument("--repl-skin", default=None, help="path to the plugin's unmodified repl_skin.py")

    sp = sub.add_parser("pipeline", help="probe -> surface -> scaffold -> validate")
    sp.add_argument("--target", default=None, help="filesystem target: app dir, .app, .asar, or binary")
    sp.add_argument("--url", default=None, help="website URL (launches a browser)")
    sp.add_argument("--browser", default=None, help="Chrome-family browser executable (URL mode)")
    sp.add_argument("--cdp", default=None, help="loopback CDP endpoint of a running debug browser (URL mode)")
    sp.add_argument("--wait-ms", type=int, default=2000, dest="wait_ms")
    sp.add_argument("--name", default=None)
    sp.add_argument("--out", default="forge-out")
    sp.add_argument("--author", default="auto-cli")
    sp.add_argument("--repl-skin", default=None, help="path to the plugin's unmodified repl_skin.py")
    sp.add_argument("--probe", action="append")
    sp.add_argument("--timeout", type=int, default=600)

    sp = sub.add_parser("validate", help="check a harness against CLI-Anything rules")
    sp.add_argument("--dir", required=True)

    sp = sub.add_parser("verify", help="prove recon findings against the live target")
    sp.add_argument("--bundle", required=True, help="evidence-bundle.json from probe/pipeline")
    sp.add_argument("--dir", required=True, help="generated scaffold dir (agent-harness)")
    sp.add_argument("--timeout", type=float, default=5.0)

    sp = sub.add_parser(
        "complete",
        help="generate the domain surface (table groups, endpoint commands) from verified evidence",
    )
    sp.add_argument("--bundle", required=True)
    sp.add_argument("--dir", required=True, help="generated scaffold dir (agent-harness)")

    sp = sub.add_parser("deep", help="hand verified findings to a coding agent for richer commands and workflows")
    sp.add_argument("--bundle", required=True)
    sp.add_argument("--dir", required=True, help="generated scaffold dir with verification.json")
    sp.add_argument("--agent", choices=["codex", "claude"], required=True)
    sp.add_argument("--task", default="", help="workflow priorities for the coding agent")
    sp.add_argument("--timeout", type=int, default=900, help="agent timeout in seconds")
    sp.add_argument("--prepare-only", action="store_true", help="write handoff artifacts without running an agent")

    sp = sub.add_parser(
        "auto",
        help="fully automatic: recon -> scaffold -> verify -> drive -> complete -> validate",
    )
    sp.add_argument("--target", default=None)
    sp.add_argument("--url", default=None)
    sp.add_argument("--name", default=None)
    sp.add_argument("--out", default="forge-auto")
    sp.add_argument("--author", default="auto-cli")
    sp.add_argument("--repl-skin", default=None)
    sp.add_argument("--browser", default=None)
    sp.add_argument("--cdp", default=None)
    sp.add_argument("--wait-ms", type=int, default=2000, dest="wait_ms")
    sp.add_argument("--wait", type=float, default=25.0, help="seconds to wait for a launched app")
    sp.add_argument("--inspector", default=None)
    sp.add_argument("--launch", default=None, help='command that starts the app, e.g. "node --inspect=9229 app.js"')
    sp.add_argument("--cwd", default=None)
    sp.add_argument("--observe-ms", type=int, default=1500, dest="observe_ms")
    sp.add_argument("--probe", action="append")
    sp.add_argument("--timeout", type=int, default=600)
    sp.add_argument("--repair", action="store_true", help="one regeneration pass if validation fails")
    sp.add_argument("--agent", choices=["codex", "claude"], default=None, help="optional deep workflow generation after base validation")
    sp.add_argument("--agent-task", default="", help="workflow priorities for the coding agent")
    sp.add_argument("--agent-timeout", type=int, default=900, help="agent timeout in seconds")
    sp.add_argument("--agent-prepare-only", action="store_true", help="prepare the handoff without running the selected agent")
    sp.add_argument("--gui-process", default=None, dest="gui_process", help="GUI leg: window ClassName")
    sp.add_argument("--gui-pid", type=int, default=None, dest="gui_pid")
    sp.add_argument("--gui-title", default=None, dest="gui_title", help="GUI leg: window title substring")

    sp = sub.add_parser(
        "drive",
        help="attach to the running app, exercise channels, upgrade the scaffold",
    )
    sp.add_argument("--bundle", required=True)
    sp.add_argument("--dir", required=True, help="generated scaffold dir (agent-harness)")
    sp.add_argument("--inspector", default=None, help="loopback Inspector endpoint, e.g. http://127.0.0.1:9229")
    sp.add_argument(
        "--launch",
        default=None,
        help='command that starts the app with an inspector, e.g. "node --inspect=9229 app.js"',
    )
    sp.add_argument("--cwd", default=None, help="working directory for --launch")
    sp.add_argument("--channel", action="append", help="channel to drive (default: all recon channels)")
    sp.add_argument("--wait", type=float, default=20.0, help="seconds to wait for the inspector")
    sp.add_argument("--observe-ms", type=int, default=1500, dest="observe_ms")
    sp.add_argument("--timeout", type=int, default=120)

    sp = sub.add_parser(
        "gui",
        help="Windows GUI driving via UIA: capture the tree, drive actions, upgrade the scaffold",
    )
    sp.add_argument("--pid", type=int, default=None)
    sp.add_argument("--process", default=None, help="window ClassName, e.g. Notepad")
    sp.add_argument("--title", default=None, help="window title substring")
    sp.add_argument("--window-id", type=int, default=None, dest="window_id", help="native window handle (HWND) for the REA backend")
    sp.add_argument("--backend", choices=["auto", "rea", "local"], default="auto", help="GUI backend: REA provider (preferred), built-in UIA, or auto")
    sp.add_argument("--tree", action="store_true", help="print the full actionable tree")
    sp.add_argument("--invoke", action="append", help="UIA path to invoke (requires --yes)")
    sp.add_argument("--select", action="append", help="UIA path to select (requires --yes)")
    sp.add_argument("--set-text", action="append", help="path=value to set (requires --yes)")
    sp.add_argument("--yes", action="store_true", help="confirm driving actions")
    sp.add_argument("--allow-destructive", action="store_true")
    sp.add_argument("--depth", type=int, default=6)
    sp.add_argument("--dir", default=None, help="scaffold dir to upgrade with the ui group")
    sp.add_argument("--out", default="forge-gui")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {
        "doctor": cmd_doctor,
        "probe": cmd_probe,
        "surface": cmd_surface,
        "scaffold": cmd_scaffold,
        "pipeline": cmd_pipeline,
        "validate": cmd_validate,
        "verify": cmd_verify,
        "drive": cmd_drive,
        "complete": cmd_complete,
        "deep": cmd_deep,
        "auto": cmd_auto,
        "gui": cmd_gui,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
