"""Windows GUI driving via UI Automation.

REA's native-UI observation is macOS-only, so on Windows the forge drives UIA
itself - same division of labor as `drive`: the forge acts, and every action
is recorded with before/after evidence (UIA state + window screenshot) so a
reviewer can audit what happened.

Requires the extra: pip install uiautomation   (pip install auto-cli[gui])

Safety model:
  - `tree` and `menus` are read-only
  - every mutating action (invoke / set value / select) requires --yes
  - menu items matching save/close/delete/exit/remove/format are refused
    even with --yes unless --allow-destructive is given
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    import uiautomation as auto

    _HAS_UIA = True
except ImportError:  # pragma: no cover - platform dependent
    auto = None
    _HAS_UIA = False

DESTRUCTIVE_RE = re.compile(
    r"save|close|delete|remove|exit|quit|format|discard|overwrite|replace",
    re.IGNORECASE,
)


class GuiError(Exception):
    pass


@dataclass
class UiNode:
    path: str  # parent chain: "Window > MenuBar > File"
    control_type: str
    name: str
    patterns: list[str]
    depth: int
    rect: tuple[int, int, int, int] | None = None
    # integer sibling path when this node came from a REA capture
    rea_path: list[int] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.rect:
            d["rect"] = list(self.rect)
        else:
            d.pop("rect")
        return d

    @property
    def command_name(self) -> str:
        """Derive a CLI-safe command name from the node path."""
        leaf = self.path.split(" > ")[-1] if self.path else self.name
        name = re.sub(r"[^a-z0-9]+", "-", leaf.lower()).strip("-")
        return name or "node"


@dataclass
class GuiAction:
    action: str  # invoke | expand | collapse | set-value | select
    path: str
    value: str = ""
    ok: bool = False
    detail: str = ""
    screenshot: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GuiReport:
    process: str
    pid: int = 0
    window: str = ""
    nodes: list[UiNode] = field(default_factory=list)
    actions: list[GuiAction] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "process": self.process,
            "pid": self.pid,
            "window": self.window,
            "nodes": [n.to_dict() for n in self.nodes],
            "actions": [a.to_dict() for a in self.actions],
            "limitations": self.limitations,
        }

    def markdown(self) -> str:
        lines = [
            f"# GUI drive report: {self.process}",
            "",
            f"window: `{self.window}` (pid {self.pid})",
            "",
            "## Actionable surface",
            "",
            "| path | type | patterns |",
            "| --- | --- | --- |",
        ]
        for n in self.nodes:
            if n.patterns:
                lines.append(f"| {n.path} | {n.control_type} | {', '.join(n.patterns)} |")
        lines += ["", "## Actions", ""]
        for a in self.actions:
            mark = "ok" if a.ok else "FAIL"
            lines.append(f"- [{mark}] {a.action} {a.path} {a.detail[:60]}")
        if self.limitations:
            lines += ["", "## Limitations", ""]
            lines += [f"- {l}" for l in self.limitations]
        lines.append("")
        return "\n".join(lines)


def _require_uia() -> None:
    if not _HAS_UIA:
        raise GuiError(
            "uiautomation not installed. Run: pip install uiautomation "
            "(or pip install auto-cli[gui])"
        )


def find_window(
    pid: int | None = None,
    process: str | None = None,
    title_contains: str | None = None,
):
    """Find a top-level window by pid, process name, or title substring."""
    _require_uia()
    for w in auto.GetRootControl().GetChildren():
        if pid and w.ProcessId == pid:
            return w
        if process and (w.ClassName or "").lower() == process.lower():
            return w
        if title_contains and title_contains.lower() in (w.Name or "").lower():
            return w
    return None


def find_window_by_handle(window_id: int):
    """Locate a top-level window by its native handle."""
    _require_uia()
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.NativeWindowHandle == window_id:
                return w
        except Exception:
            continue
    return None


def capture_tree(window, maxdepth: int = 6, maxnodes: int = 400) -> list[UiNode]:
    """Walk the control view into normalized nodes with pattern capabilities."""
    _require_uia()
    nodes: list[UiNode] = []
    window_name = window.Name or "window"

    def walk(ctrl, chain: list[str], depth: int) -> None:
        if depth > maxdepth or len(nodes) >= maxnodes:
            return
        for child in ctrl.GetChildren():
            name = child.Name or ""
            ct = child.ControlTypeName or ""
            pats = []
            for pname in ("Invoke", "Value", "ExpandCollapse", "SelectionItem", "Toggle"):
                try:
                    if getattr(child, f"Get{pname}Pattern", lambda: None)():
                        pats.append(pname)
                except Exception:
                    pass
            rect = None
            try:
                r = child.BoundingRectangle
                rect = (r.left, r.top, r.right, r.bottom)
            except Exception:
                pass
            if name or pats:
                path = " > ".join(chain + [name or ct])
                nodes.append(
                    UiNode(
                        path=path,
                        control_type=ct,
                        name=name,
                        patterns=pats,
                        depth=depth,
                        rect=rect,
                    )
                )
            walk(child, chain + [name or ct], depth + 1)

    walk(window, [window_name], 1)
    return nodes


def _find_node(window, path: str):
    """Resolve a path to a live control.

    Accepts a full path ("Window > A > B") or any unique suffix of it
    ("A > B"), so callers do not need to track the window title.
    """
    _require_uia()
    parts = [p for p in path.split(" > ") if p]
    if not parts:
        return None
    found = []

    def walk(ctrl, chain: list[str], depth: int) -> None:
        if depth > 8:
            return
        for child in ctrl.GetChildren():
            name = child.Name or ""
            ct = child.ControlTypeName or ""
            full = chain + [name]
            if (
                name == parts[-1]
                and " > ".join(full) == " > ".join(parts)
                or " > ".join(full[-len(parts):]) == " > ".join(parts)
            ):
                found.append(child)
            walk(child, chain + [name or ct], depth + 1)

    walk(window, [window.Name or "window"], 1)
    return found[0] if found else None


def screenshot(window, out_path: str | Path) -> str:
    _require_uia()
    try:
        window.CaptureToImage(str(out_path))
        return str(out_path)
    except Exception as e:  # pragma: no cover
        return f"screenshot failed: {e}"


def act(
    window,
    action: str,
    path: str,
    value: str = "",
    yes: bool = False,
    allow_destructive: bool = False,
    shot_dir: str | Path | None = None,
) -> GuiAction:
    """Perform one UIA action with before/after evidence."""
    result = GuiAction(action=action, path=path, value=value)
    node = _find_node(window, path)
    if node is None:
        result.detail = "node not found in the live tree"
        return result

    if action in ("invoke", "set-value", "select") and not yes:
        result.detail = f"refused: {action} requires --yes"
        return result
    if DESTRUCTIVE_RE.search(path) and not allow_destructive:
        result.detail = "refused: destructive-looking path (use --allow-destructive to override)"
        return result

    before = ""
    try:
        before = node.CurrentValue() if node.GetValuePattern() else (node.Name or "")
    except Exception:
        before = node.Name or ""

    try:
        if action == "invoke":
            node.GetInvokePattern().Invoke()
        elif action == "expand":
            node.GetExpandCollapsePattern().Expand()
        elif action == "collapse":
            node.GetExpandCollapsePattern().Collapse()
        elif action == "select":
            node.GetSelectionItemPattern().Select()
        elif action == "set-value":
            node.GetValuePattern().SetValue(value)
        else:
            result.detail = f"unknown action: {action}"
            return result
        result.ok = True
        result.detail = f"before: {before[:60]!r}"
    except Exception as e:
        result.detail = f"{type(e).__name__}: {e}"
        return result

    if shot_dir:
        time.sleep(0.3)
        out = Path(shot_dir) / f"gui-{len(path)}-{int(time.time())}.png"
        result.screenshot = screenshot(window, out)
    return result


def menu_paths(nodes: list[UiNode]) -> list[str]:
    """Expandable menu chains - the app's command surface.

    Pattern ids arrive lowercase from REA and PascalCase from the local UIA
    driver, so compare case-insensitively.
    """
    return [
        n.path
        for n in nodes
        if any(p.lower() == "expandcollapse" for p in n.patterns)
    ]


# --- REA delegation ---------------------------------------------------------
# The Windows UI provider lives in the rea-windows repo and installs into a
# REA checkout. When it is available, the forge delegates to it so GUI
# evidence is a real REA evidence envelope; gui.py's own UIA code stays as
# the standalone fallback for hosts without the provider.

REA_UI_OPERATIONS = ("observe-native-ui", "capture-native-ui-scenario")


def window_executable(pid: int) -> str:
    """Resolve a process's executable path for REA's target argument."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def window_executable(pid: int) -> str:
    """Resolve a process's executable path for REA's target argument."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def _rea_nodes_to_ui(
    nodes: list[dict], window_title: str = ""
) -> list[UiNode]:
    """Convert REA envelope nodes (integer paths) into string-path UiNodes.

    REA addresses nodes by sibling index; the forge's surface (and its
    generated harnesses) address them by name chain, so rebuild each node's
    ancestor chain from the flat list by longest-prefix matching. REA's root
    node carries a null title - the window title lives in the envelope's
    window block - so the root falls back to it.
    """
    by_path = {tuple(node.get("path") or ()): node for node in nodes}
    out: list[UiNode] = []
    for node in nodes:
        path = tuple(node.get("path") or ())
        chain: list[str] = []
        cursor = path
        while cursor:
            ancestor = by_path.get(cursor)
            if ancestor is None:
                break
            label = ancestor.get("title") or ancestor.get("role") or "node"
            if len(cursor) == 1 and window_title:
                label = window_title
            chain.append(label)
            cursor = cursor[:-1]
        chain.reverse()
        out.append(
            UiNode(
                path=" > ".join(chain) or (node.get("title") or "window"),
                control_type=node.get("role") or "",
                name=node.get("title") or "",
                patterns=[str(a).lower() for a in (node.get("actions") or [])],
                depth=len(path),
                rea_path=list(path),
            )
        )
    return out


def probe_rea_gui(rea, window_id: int, pid: int = 0) -> bool:
    """Cheap availability probe: one tiny observation through the provider."""
    from . import rea_client

    # the same interpreter serves REA's helper - no second Python install
    os.environ.setdefault("REA_WINDOWS_PYTHON", sys.executable)
    target = window_executable(pid) if pid else ""
    if not target:
        return False
    result = rea_client.run_probe(
        rea,
        "observe-native-ui",
        [target, "--pid", str(pid), "--window-id", str(window_id), "--max-nodes", "1"],
        timeout=120,
    )
    if not result.ok or not result.envelope:
        return False
    provider = (result.envelope.get("provider") or {}).get("id", "")
    return provider == "native-windows"


def capture_via_rea(rea, window_id: int, pid: int = 0, max_nodes: int = 400) -> list[UiNode]:
    """Capture the tree through REA's native-windows provider."""
    from . import rea_client

    os.environ.setdefault("REA_WINDOWS_PYTHON", sys.executable)
    target = window_executable(pid) if pid else ""
    if not target:
        raise GuiError("cannot resolve the window's executable for REA's target argument")
    result = rea_client.run_probe(
        rea,
        "observe-native-ui",
        [
            target,
            "--pid",
            str(pid),
            "--window-id",
            str(window_id),
            "--max-nodes",
            str(max_nodes),
        ],
        timeout=300,
    )
    if not result.ok or not result.envelope:
        raise GuiError(f"REA observation failed: {str(result.error)[:200]}")
    envelope = result.envelope
    if (envelope.get("provider") or {}).get("id") != "native-windows":
        raise GuiError("REA routed observe-native-ui to a non-Windows provider")
    nodes = ((envelope.get("normalized_result") or {}).get("initial") or {}).get("nodes")
    if nodes is None:
        nodes = (envelope.get("normalized_result") or {}).get("nodes")
    if not isinstance(nodes, list):
        raise GuiError("REA envelope carried no node list")
    window_title = ""
    normalized = envelope.get("normalized_result") or {}
    snapshot = normalized.get("initial") or normalized
    window_title = (snapshot.get("window") or {}).get("title") or ""
    return _rea_nodes_to_ui(nodes, window_title)


def act_via_rea(
    rea,
    window_id: int,
    action: str,
    path: str,
    value: str = "",
    pid: int = 0,
    yes: bool = False,
    allow_destructive: bool = False,
    shot_dir: str | Path | None = None,
    nodes: list[UiNode] | None = None,
) -> GuiAction:
    """Drive one action through REA, recording its evidence envelope."""
    from . import rea_client

    result = GuiAction(action=action, path=path, value=value)
    int_path = None
    if nodes:
        for node in nodes:
            if node.path == path:
                int_path = getattr(node, "rea_path", None)
                break
    if int_path is None:
        result.detail = "node not present in the last REA capture"
        return result
    if action in ("invoke", "set-value", "select") and not yes:
        result.detail = f"refused: {action} requires --yes"
        return result
    if DESTRUCTIVE_RE.search(path) and not allow_destructive:
        result.detail = "refused: destructive-looking path (use --allow-destructive to override)"
        return result

    kind = {"invoke": "click", "set-value": "key-entry", "select": "click"}[action]
    step = {"kind": kind, "path": int_path}
    if kind == "key-entry":
        step["text"] = value
    os.environ.setdefault("REA_WINDOWS_PYTHON", sys.executable)
    target = window_executable(pid) if pid else ""
    if not target:
        result.detail = "cannot resolve the window's executable for REA's target argument"
        return result
    probe = rea_client.run_probe(
        rea,
        "capture-native-ui-scenario",
        [
            target,
            "--pid",
            str(pid),
            "--window-id",
            str(window_id),
            "--steps",
            json.dumps([step]),
        ],
        timeout=300,
    )
    if not probe.ok or not probe.envelope:
        result.detail = f"REA scenario failed: {str(probe.error)[:160]}"
        return result
    normalized = probe.envelope.get("normalized_result") or {}
    steps = normalized.get("steps") or []
    if not steps:
        result.detail = "REA returned no step evidence"
        return result
    outcome = steps[0]
    result.ok = outcome.get("outcome") == "completed"
    result.detail = str(outcome.get("reason") or "completed")
    if shot_dir and result.ok:
        shot = Path(shot_dir) / f"rea-gui-{int(time.time())}.png"
        result.screenshot = str(shot)
        result.detail += f" | evidence: {probe.envelope.get('evidence_id', '')[:16]}"
    return result


UIA_RUNNER_PY = '''#!/usr/bin/env python3
"""Self-contained UIA runner for this harness.

Subcommands: tree | menus | invoke <path> | select <path> | set-value <path> <text>
Finds the target window by process class or title. Every driving action
requires --yes. Requires: pip install uiautomation
"""

import argparse
import json
import re
import sys

try:
    import uiautomation as auto
except ImportError:
    print(json.dumps({"ok": False, "detail": "pip install uiautomation"}))
    sys.exit(3)

DESTRUCTIVE_RE = re.compile(
    r"save|close|delete|remove|exit|quit|format|discard|overwrite|replace",
    re.IGNORECASE,
)


def find_window(process=None, title=None, pid=None):
    for w in auto.GetRootControl().GetChildren():
        if pid and w.ProcessId == pid:
            return w
        if process and (w.ClassName or "").lower() == process.lower():
            return w
        if title and title.lower() in (w.Name or "").lower():
            return w
    return None


def capture(window, maxdepth=6):
    nodes = []

    def walk(ctrl, chain, depth):
        if depth > maxdepth or len(nodes) > 400:
            return
        for child in ctrl.GetChildren():
            name = child.Name or ""
            ct = child.ControlTypeName or ""
            pats = []
            for pname in ("Invoke", "Value", "ExpandCollapse", "SelectionItem", "Toggle"):
                try:
                    if getattr(child, f"Get{pname}Pattern", lambda: None)():
                        pats.append(pname)
                except Exception:
                    pass
            if name or pats:
                nodes.append(
                    {
                        "path": " > ".join(chain + [name or ct]),
                        "control_type": ct,
                        "name": name,
                        "patterns": pats,
                    }
                )
            walk(child, chain + [name or ct], depth + 1)

    walk(window, [window.Name or "window"], 1)
    return nodes


def resolve(window, path):
    parts = [p for p in path.split(" > ") if p]
    if not parts:
        return None
    found = []

    def walk(ctrl, chain, depth):
        if depth > 8:
            return
        for child in ctrl.GetChildren():
            name = child.Name or ""
            ct = child.ControlTypeName or ""
            full = chain + [name]
            if (
                name == parts[-1]
                and " > ".join(full) == " > ".join(parts)
                or " > ".join(full[-len(parts):]) == " > ".join(parts)
            ):
                found.append(child)
            walk(child, chain + [name or ct], depth + 1)

    walk(window, [window.Name or "window"], 1)
    return found[0] if found else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--process", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--allow-destructive", action="store_true")
    a = ap.parse_args()

    window = find_window(a.process, a.title, a.pid)
    if not window:
        print(json.dumps({"ok": False, "detail": "target window not found"}))
        return 2

    if a.cmd == "tree":
        print(json.dumps({"ok": True, "window": window.Name, "nodes": capture(window)}))
        return 0
    if a.cmd == "menus":
        nodes = capture(window)
        menus = [n["path"] for n in nodes if "ExpandCollapse" in n["patterns"]]
        print(json.dumps({"ok": True, "menus": menus}))
        return 0

    if not a.yes:
        print(json.dumps({"ok": False, "detail": "driving requires --yes"}))
        return 2
    path = a.args[0] if a.args else ""
    if DESTRUCTIVE_RE.search(path) and not a.allow_destructive:
        print(json.dumps({"ok": False, "detail": "destructive-looking path refused"}))
        return 2
    node = resolve(window, path)
    if not node:
        print(json.dumps({"ok": False, "detail": "node not found: " + path}))
        return 2
    try:
        if a.cmd == "invoke":
            node.GetInvokePattern().Invoke()
        elif a.cmd == "select":
            node.GetSelectionItemPattern().Select()
        elif a.cmd == "set-value":
            node.GetValuePattern().SetValue(a.args[1] if len(a.args) > 1 else "")
        else:
            print(json.dumps({"ok": False, "detail": "unknown cmd"}))
            return 2
    except Exception as e:
        print(json.dumps({"ok": False, "detail": f"{type(e).__name__}: {e}"}))
        return 2
    print(json.dumps({"ok": True, "action": a.cmd, "path": path}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

UI_CLI_TEMPLATE = '''

# ---- GUI surface added by auto-cli gui -------------------------------

import subprocess as _sp
import sys as _sys


def _uia_run(args: list[str]) -> dict:
    """Shell out to the harness's own UIA runner with the same safety gates."""
    from pathlib import Path as _Path

    runner = _Path(__file__).parent / "utils" / "uia_runner.py"
    cmd = [_sys.executable, str(runner), *args]
    if backend.TARGET_WINDOW_PROCESS:
        cmd += ["--process", backend.TARGET_WINDOW_PROCESS]
    if backend.TARGET_WINDOW_TITLE:
        cmd += ["--title", backend.TARGET_WINDOW_TITLE]
    try:
        proc = _sp.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, _sp.SubprocessError) as e:
        return {"ok": False, "detail": str(e)}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "detail": (proc.stderr or proc.stdout)[:200]}


@main.command("ui-tree")
@click.pass_context
def ui_tree(ctx: click.Context) -> None:
    """Dump the live UIA tree (read-only)."""
    _emit(ctx, _uia_run(["tree"]))


@main.command("ui-menus")
@click.pass_context
def ui_menus(ctx: click.Context) -> None:
    """List expandable menu paths (read-only)."""
    _emit(ctx, _uia_run(["menus"]))


@main.command("ui-invoke")
@click.argument("path")
@click.option("--yes", is_flag=True, help="confirm the action")
@click.pass_context
def ui_invoke(ctx: click.Context, path: str, yes: bool) -> None:
    """Invoke one UIA element. Requires --yes."""
    if not yes:
        _fail("refusing to drive the GUI without --yes")
    _emit(ctx, _uia_run(["invoke", path, "--yes"]))


@main.command("ui-select")
@click.argument("path")
@click.option("--yes", is_flag=True, help="confirm the action")
@click.pass_context
def ui_select(ctx: click.Context, path: str, yes: bool) -> None:
    """Select one UIA element (tab, list item). Requires --yes."""
    if not yes:
        _fail("refusing to drive the GUI without --yes")
    _emit(ctx, _uia_run(["select", path, "--yes"]))


@main.command("ui-set-text")
@click.argument("path")
@click.argument("text")
@click.option("--yes", is_flag=True, help="confirm the action")
@click.pass_context
def ui_set_text(ctx: click.Context, path: str, text: str, yes: bool) -> None:
    """Set a UIA Value element's text. Requires --yes."""
    if not yes:
        _fail("refusing to drive the GUI without --yes")
    _emit(ctx, _uia_run(["set-value", path, text, "--yes"]))
'''

UI_TESTS = '''

def test_ui_commands_exist():
    """The GUI surface must be present and gated."""
    from cli_anything.{module} import {module}_cli

    names = {{c.name for c in {module}_cli.main.commands.values()}}
    assert {{"ui-tree", "ui-menus", "ui-invoke", "ui-select", "ui-set-text"}} <= names
'''


def upgrade_scaffold(root: Path, report: GuiReport) -> bool:
    """Add the ui command group to a scaffolded harness. Returns True if changed."""
    pkg = next(root.glob("cli_anything/*"), None)
    if not pkg:
        return False
    module = pkg.name
    cli_file = pkg / f"{module}_cli.py"
    src = cli_file.read_text(encoding="utf-8")
    if "ui-tree" in src:
        return False
    anchor = '\n\nif __name__ == "__main__":'
    if anchor not in src:
        return False
    src = src.replace(anchor, UI_CLI_TEMPLATE + anchor)
    cli_file.write_text(src, encoding="utf-8")

    # self-contained runner + target constants in the backend
    utils = pkg / "utils"
    utils.mkdir(parents=True, exist_ok=True)
    (utils / "uia_runner.py").write_text(UIA_RUNNER_PY, encoding="utf-8")

    backend = pkg / "core" / "backend.py"
    bsrc = backend.read_text(encoding="utf-8")
    bsrc = bsrc.replace(
        "ALLOWED_ORIGINS = ",
        f"TARGET_WINDOW_PROCESS = {report.process!r}\n"
        f"TARGET_WINDOW_TITLE = {report.window!r}\n"
        "ALLOWED_ORIGINS = ",
        1,
    )
    backend.write_text(bsrc, encoding="utf-8")

    tests = pkg / "tests" / "test_core.py"
    tsrc = tests.read_text(encoding="utf-8")
    tsrc += UI_TESTS.format(module=module)
    tests.write_text(tsrc, encoding="utf-8")

    skill = pkg / "skills" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    menus = "\n".join(f"- `{p}`" for p in menu_paths(report.nodes)[:20]) or "- (none discovered)"
    text = text.replace(
        "## Rules for agents",
        "## GUI surface (driven via UIA, see GUI-REPORT.md)\n\n"
        "Read-only:\n\n```bash\n"
        f"{'cli-anything-' + module.replace('_', '-')} ui-tree\n"
        f"{'cli-anything-' + module.replace('_', '-')} ui-menus\n"
        "```\n\n"
        "Driving (all require --yes):\n\n```bash\n"
        f"{'cli-anything-' + module.replace('_', '-')} ui-invoke \"<path>\" --yes\n"
        f"{'cli-anything-' + module.replace('_', '-')} ui-select \"<path>\" --yes\n"
        f"{'cli-anything-' + module.replace('_', '-')} ui-set-text \"<path>\" \"text\" --yes\n"
        "```\n\n"
        "Discovered menu paths:\n\n" + menus + "\n\n## Rules for agents",
    )
    skill.write_text(text, encoding="utf-8")
    return True


def write_report(report: GuiReport, scaffold_dir: str | Path) -> Path:
    root = Path(scaffold_dir)
    if root.name != "agent-harness":
        found = next(root.rglob("agent-harness"), None)
        if found:
            root = found
    p = root / "GUI-REPORT.md"
    p.write_text(report.markdown(), encoding="utf-8")
    (root / "gui.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return p
