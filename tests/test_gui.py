"""Tests for the Windows GUI driver (gui command)."""

import json
import py_compile
import sys
import types
from unittest.mock import Mock

import pytest

from auto_cli import gui as gui_mod, evidence as ev, scaffold, surface

@pytest.fixture()
def built(tmp_path):
    findings = [ev.Finding(kind="ipc_channel", value="lib:load", source_command="analyze")]
    bundle = ev.EvidenceBundle(target=str(tmp_path / "app"), findings=findings)
    plan = surface.classify(bundle)
    root = scaffold.generate(plan, bundle, tmp_path / "out", name="gui-app")
    return bundle, plan, root


def _report(nodes):
    r = gui_mod.GuiReport(process="Notepad", pid=1234, window="test - Notepad")
    r.nodes = nodes
    return r


def _node(path, patterns, ct="MenuItemControl"):
    return gui_mod.UiNode(path=path, control_type=ct, name=path.split(" > ")[-1], patterns=patterns, depth=2)


def test_menu_paths_from_expandable_nodes():
    r = _report(
        [
            _node("Notepad > File", ["Invoke", "ExpandCollapse"]),
            _node("Notepad > Text editor", ["Value"], ct="DocumentControl"),
            _node("Notepad > Close", ["Invoke"], ct="ButtonControl"),
        ]
    )
    assert gui_mod.menu_paths(r.nodes) == ["Notepad > File"]


def test_command_name_derivation():
    n = _node("Notepad > View > Zoom In", ["Invoke"])
    assert n.command_name == "zoom-in"


def test_destructive_pattern():
    assert gui_mod.DESTRUCTIVE_RE.search("Notepad > File > Save")
    assert gui_mod.DESTRUCTIVE_RE.search("Notepad > File > Exit")
    assert not gui_mod.DESTRUCTIVE_RE.search("Notepad > View > Zoom In")


def test_upgrade_scaffold_adds_ui_group(tmp_path, built):
    bundle, plan, root = built
    r = _report([_node("Notepad > File", ["Invoke", "ExpandCollapse"])])
    assert gui_mod.upgrade_scaffold(root, r) is True
    cli = (root / "cli_anything" / "gui_app" / "gui_app_cli.py").read_text()
    assert "def ui_tree" in cli
    assert "def ui_invoke" in cli
    assert "TARGET_WINDOW_PROCESS" in (root / "cli_anything" / "gui_app" / "core" / "backend.py").read_text()
    assert (root / "cli_anything" / "gui_app" / "utils" / "uia_runner.py").exists()
    # idempotent
    assert gui_mod.upgrade_scaffold(root, r) is False


def test_generated_uia_runner_compiles(tmp_path, built):
    bundle, plan, root = built
    r = _report([_node("Notepad > File", ["Invoke", "ExpandCollapse"])])
    gui_mod.upgrade_scaffold(root, r)
    runner = root / "cli_anything" / "gui_app" / "utils" / "uia_runner.py"
    py_compile.compile(str(runner), doraise=True)


def test_generated_ui_tests_pass(tmp_path, built):
    import subprocess
    import sys

    bundle, plan, root = built
    r = _report([_node("Notepad > File", ["Invoke", "ExpandCollapse"])])
    gui_mod.upgrade_scaffold(root, r)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(root / "cli_anything/gui_app/tests/test_core.py"), "-q", "--no-header"],
        capture_output=True,
        text=True,
        timeout=300,
        env={"PYTHONPATH": str(root), "PATH": "C:\\Windows\\System32", "SYSTEMROOT": "C:\\Windows"},
    )
    assert proc.returncode == 0, proc.stdout[-1500:]


def test_skill_documents_gui_surface(tmp_path, built):
    bundle, plan, root = built
    r = _report([_node("Notepad > File", ["Invoke", "ExpandCollapse"])])
    gui_mod.upgrade_scaffold(root, r)
    skill = (root / "cli_anything" / "gui_app" / "skills" / "SKILL.md").read_text()
    assert "GUI surface" in skill
    assert "Notepad > File" in skill


def test_report_markdown():
    r = _report([_node("Notepad > File", ["Invoke", "ExpandCollapse"])])
    r.actions.append(gui_mod.GuiAction(action="invoke", path="Notepad > File", ok=True, detail="ok"))
    md = r.markdown()
    assert "GUI drive report" in md
    assert "Notepad > File" in md


def test_write_report(tmp_path, built):
    bundle, plan, root = built
    p = gui_mod.write_report(_report([]), root)
    assert p.name == "GUI-REPORT.md"
    assert (root / "gui.json").exists()


class FakeControl:
    def __init__(self, name="", control_type="PaneControl", children=(), pid=1, class_name="App"):
        self.Name = name
        self.ControlTypeName = control_type
        self.ClassName = class_name
        self.ProcessId = pid
        self.children = list(children)
        self.invoke = Mock()

    def GetChildren(self):
        return self.children

    def GetInvokePattern(self):
        return types.SimpleNamespace(Invoke=self.invoke)


@pytest.fixture(params=["local", "generated"])
def driver(request, monkeypatch):
    """Exercise both the package and its independently shipped runner."""
    root = FakeControl()
    fake_auto = types.SimpleNamespace(GetRootControl=lambda: root)
    if request.param == "local":
        monkeypatch.setattr(gui_mod, "auto", fake_auto)
        monkeypatch.setattr(gui_mod, "_HAS_UIA", True)
        return types.SimpleNamespace(
            root=root, find=lambda **kw: gui_mod.find_window(**kw),
            resolve=gui_mod._find_node, capture=gui_mod.capture_tree, error=gui_mod.GuiError,
            module=gui_mod,
        )
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    runner = types.ModuleType("test_uia_runner")
    exec(compile(gui_mod.UIA_RUNNER_PY, "uia_runner.py", "exec"), runner.__dict__)
    return types.SimpleNamespace(
        root=root,
        find=lambda pid=None, process=None, title_contains=None: runner.find_window(process, title_contains, pid),
        resolve=runner.resolve, capture=runner.capture, error=ValueError, module=runner,
    )


def test_find_window_returns_none_for_bogus(driver):
    assert driver.find(process="NoSuchWindowClass12345") is None


def test_find_window_requires_every_selector(driver):
    first = FakeControl("First", pid=1)
    target = FakeControl("Target", pid=2)
    driver.root.children = [first, target]
    assert driver.find(process="App", title_contains="Target", pid=2) is target
    assert driver.find(process="App", title_contains="Target", pid=1) is None


def test_find_window_refuses_ambiguous_target(driver):
    driver.root.children = [FakeControl("First"), FakeControl("Second")]
    with pytest.raises(driver.error, match="multiple windows"):
        driver.find(process="App")


def test_resolve_matches_unnamed_controls_as_captured(driver):
    target = FakeControl(control_type="ButtonControl")
    window = FakeControl("Window", children=[target])
    node = driver.capture(window)[0]
    path = node.path if isinstance(node, gui_mod.UiNode) else node["path"]
    assert path == "Window > ButtonControl"
    assert driver.resolve(window, path) is target
    assert driver.resolve(window, "ButtonControl") is target


@pytest.mark.parametrize("path", ["OK", "Window > OK"])
def test_resolve_refuses_duplicate_paths(driver, path):
    window = FakeControl("Window", children=[FakeControl("OK"), FakeControl("OK")])
    with pytest.raises(driver.error, match="ambiguous node"):
        driver.resolve(window, path)


def test_capture_enforces_limit_across_siblings(driver):
    window = FakeControl("Window", children=[FakeControl(str(i)) for i in range(410)])
    assert len(driver.capture(window)) == 400


def test_ambiguous_local_action_does_not_invoke(monkeypatch):
    monkeypatch.setattr(gui_mod, "_HAS_UIA", True)
    targets = [FakeControl("OK"), FakeControl("OK")]
    result = gui_mod.act(FakeControl("Window", children=targets), "invoke", "OK", yes=True)
    assert not result.ok
    assert "ambiguous" in result.detail
    for target in targets:
        target.invoke.assert_not_called()


def test_generated_runner_reports_ambiguous_target(driver, monkeypatch, capsys):
    if driver.module is gui_mod:
        pytest.skip("generated runner entry point only")
    driver.root.children = [FakeControl("First"), FakeControl("Second")]
    monkeypatch.setattr(sys, "argv", ["uia_runner.py", "tree", "--process", "App"])
    assert driver.module.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert not payload["ok"]
    assert "multiple windows" in payload["detail"]


def test_write_report_creates_output_directory(tmp_path):
    report = gui_mod.write_report(_report([]), tmp_path / "new" / "gui")
    assert report.is_file()
    assert (report.parent / "gui.json").is_file()


def test_generated_ui_command_exits_nonzero_on_runner_failure(monkeypatch):
    import click
    from click.testing import CliRunner

    @click.group()
    def main():
        pass

    namespace = {"click": click, "main": main, "_emit": lambda ctx, payload: click.echo(json.dumps(payload))}
    exec(gui_mod.UI_CLI_TEMPLATE, namespace)
    monkeypatch.setitem(namespace, "_uia_run", lambda args: {"ok": False, "detail": "target window not found"})
    result = CliRunner().invoke(main, ["ui-tree"])
    assert result.exit_code == 2
    assert json.loads(result.output)["ok"] is False


@pytest.fixture(autouse=True)
def stub_rea_executable(request, monkeypatch):
    # REA tests use a fictional process id and must not query the real desktop.
    if request.node.name.startswith(("test_rea_", "test_capture_via_rea_", "test_act_via_rea_")):
        monkeypatch.setattr(gui_mod, "window_executable", lambda pid: "C:/app/App.exe")


def test_window_executable_declares_pointer_sized_process_handles(monkeypatch):
    import ctypes
    from ctypes import wintypes

    handle = 0x123456789

    def query(process_handle, flags, buf, size):
        assert process_handle == handle
        buf.value = "C:/app/App.exe"
        return True

    kernel32 = types.SimpleNamespace(
        OpenProcess=Mock(return_value=handle),
        QueryFullProcessImageNameW=Mock(side_effect=query),
        CloseHandle=Mock(return_value=True),
    )
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32), raising=False)
    assert gui_mod.window_executable(123) == "C:/app/App.exe"
    assert kernel32.OpenProcess.restype is wintypes.HANDLE
    assert kernel32.QueryFullProcessImageNameW.argtypes[0] is wintypes.HANDLE
    assert kernel32.CloseHandle.argtypes == [wintypes.HANDLE]
    kernel32.CloseHandle.assert_called_once_with(handle)


# --- REA delegation ----------------------------------------------------------

REA_ENVELOPE = {
    "provider": {"id": "native-windows", "name": "Windows native UI utilities"},
    "operation": "observe_native_ui",
    "normalized_result": {
        "initial": {
            "window": {"pid": 10516, "window_id": 787822, "executable": "C://app//App.exe", "launch_time": 1, "title": "App"},
            "nodes": [
                {"path": [0], "role": "PaneControl", "title": None, "value": None, "actions": [], "children_count": 2},
                {"path": [0, 0], "role": "MenuItemControl", "title": "File", "value": None, "actions": ["invoke", "expandcollapse"], "children_count": 0},
                {"path": [0, 1], "role": "DocumentControl", "title": "Text editor", "value": None, "actions": ["value", "text"], "children_count": 0},
            ],
            "truncated": False,
            "screenshot": None,
            "gaps": [],
        }
    },
}


class FakeRea:
    kind = "global"
    argv = ["rea"]


def test_rea_nodes_convert_to_string_paths():
    nodes = gui_mod._rea_nodes_to_ui(REA_ENVELOPE["normalized_result"]["initial"]["nodes"], window_title="App")
    assert [n.path for n in nodes] == ["App", "App > File", "App > Text editor"]
    assert nodes[1].patterns == ["invoke", "expandcollapse"]
    assert nodes[2].control_type == "DocumentControl"
    # integer paths survive for action delegation
    assert nodes[1].rea_path == [0, 0]


def test_rea_probe_accepts_only_the_windows_provider(monkeypatch):
    from auto_cli import rea_client

    def fake_run(loc, command, args, timeout=600):
        class R:
            ok = True
            envelope = REA_ENVELOPE
            error = None
        return R()

    monkeypatch.setattr(rea_client, "run_probe", fake_run)
    assert gui_mod.probe_rea_gui(FakeRea(), 787822, 10516) is True

    def fake_run_other(loc, command, args, timeout=600):
        class R:
            ok = True
            envelope = {"provider": {"id": "native-macos"}}
            error = None
        return R()

    monkeypatch.setattr(rea_client, "run_probe", fake_run_other)
    assert gui_mod.probe_rea_gui(FakeRea(), 787822, 10516) is False


def test_capture_via_rea_converts_the_envelope(monkeypatch):
    from auto_cli import rea_client

    def fake_run(loc, command, args, timeout=600):
        class R:
            ok = True
            envelope = REA_ENVELOPE
            error = None
        return R()

    monkeypatch.setattr(rea_client, "run_probe", fake_run)
    nodes = gui_mod.capture_via_rea(FakeRea(), 787822, 10516)
    assert len(nodes) == 3
    assert gui_mod.menu_paths(nodes) == ["App > File"]


def test_capture_via_rea_rejects_a_non_windows_provider(monkeypatch):
    from auto_cli import rea_client

    def fake_run(loc, command, args, timeout=600):
        class R:
            ok = True
            envelope = {"provider": {"id": "native-macos"}, "normalized_result": {"nodes": []}}
            error = None
        return R()

    monkeypatch.setattr(rea_client, "run_probe", fake_run)
    with pytest.raises(gui_mod.GuiError):
        gui_mod.capture_via_rea(FakeRea(), 787822, 10516)


def test_act_via_rea_refuses_without_yes(monkeypatch):
    from auto_cli import rea_client

    monkeypatch.setattr(rea_client, "run_probe", lambda *a, **k: pytest.fail("must not call REA"))
    nodes = gui_mod._rea_nodes_to_ui(REA_ENVELOPE["normalized_result"]["initial"]["nodes"], window_title="App")
    result = gui_mod.act_via_rea(FakeRea(), 787822, "invoke", "App > File", pid=10516, yes=False, nodes=nodes)
    assert result.ok is False
    assert "--yes" in result.detail


@pytest.mark.parametrize("path", ["App > Text editor", "Text editor"])
def test_act_via_rea_maps_to_rea_steps(monkeypatch, path, tmp_path):
    from auto_cli import rea_client
    seen = {}

    def fake_run(loc, command, args, timeout=600):
        seen["command"] = command
        seen["steps"] = json.loads(args[args.index("--steps") + 1])

        class R:
            ok = True
            envelope = {
                "provider": {"id": "native-windows"},
                "evidence_id": "ev_test",
                "normalized_result": {"steps": [{"index": 0, "kind": "key-entry", "outcome": "completed", "reason": None}]},
            }
            error = None
        return R()

    monkeypatch.setattr(rea_client, "run_probe", fake_run)
    nodes = gui_mod._rea_nodes_to_ui(REA_ENVELOPE["normalized_result"]["initial"]["nodes"], window_title="App")
    result = gui_mod.act_via_rea(
        FakeRea(), 787822, "set-value", path, "hello", pid=10516, yes=True, nodes=nodes, shot_dir=tmp_path
    )
    assert result.ok is True
    assert seen["command"] == "capture-native-ui-scenario"
    assert seen["steps"] == [{"kind": "key-entry", "path": [0, 1], "text": "hello"}]
    assert result.screenshot == ""
    assert "evidence: ev_test" in result.detail


def test_act_via_rea_refuses_destructive_paths():
    from auto_cli import rea_client
    nodes = gui_mod._rea_nodes_to_ui(
        REA_ENVELOPE["normalized_result"]["initial"]["nodes"]
        + [{"path": [0, 2], "role": "MenuItemControl", "title": "Save", "value": None, "actions": ["invoke"], "children_count": 0}],
        window_title="App",
    )
    result = gui_mod.act_via_rea(FakeRea(), 787822, "invoke", "App > Save", pid=10516, yes=True, nodes=nodes)
    assert result.ok is False
    assert "destructive" in result.detail


def test_act_via_rea_unknown_path_fails_closed():
    from auto_cli import rea_client
    nodes = gui_mod._rea_nodes_to_ui(REA_ENVELOPE["normalized_result"]["initial"]["nodes"], window_title="App")
    result = gui_mod.act_via_rea(FakeRea(), 787822, "invoke", "App > Nope", pid=10516, yes=True, nodes=nodes)
    assert result.ok is False
    assert "not present" in result.detail


def test_act_via_rea_ambiguous_path_does_not_call_provider(monkeypatch):
    from auto_cli import rea_client

    monkeypatch.setattr(rea_client, "run_probe", lambda *a, **k: pytest.fail("ambiguous paths must not drive REA"))
    nodes = [
        gui_mod.UiNode("App > OK", "ButtonControl", "OK", ["invoke"], 2, rea_path=[0, index])
        for index in range(2)
    ]
    result = gui_mod.act_via_rea(FakeRea(), 1, "invoke", "App > OK", yes=True, nodes=nodes)
    assert not result.ok
    assert "ambiguous" in result.detail


def test_act_via_rea_unknown_action_fails_without_exception(monkeypatch):
    from auto_cli import rea_client

    monkeypatch.setattr(rea_client, "run_probe", lambda *a, **k: pytest.fail("unknown actions must not drive REA"))
    nodes = gui_mod._rea_nodes_to_ui(REA_ENVELOPE["normalized_result"]["initial"]["nodes"], window_title="App")
    result = gui_mod.act_via_rea(FakeRea(), 1, "unsupported", "App > File", yes=True, nodes=nodes)
    assert not result.ok
    assert "unknown action" in result.detail
