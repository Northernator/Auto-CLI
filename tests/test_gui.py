"""Tests for the Windows GUI driver (gui command)."""

import json
import py_compile

import pytest

from auto_cli import gui as gui_mod, evidence as ev, scaffold, surface

auto = pytest.importorskip("uiautomation")


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


def test_find_window_returns_none_for_bogus():
    assert gui_mod.find_window(process="NoSuchWindowClass12345") is None


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


def test_act_via_rea_maps_to_rea_steps(monkeypatch):
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
        FakeRea(), 787822, "set-value", "App > Text editor", "hello", pid=10516, yes=True, nodes=nodes
    )
    assert result.ok is True
    assert seen["command"] == "capture-native-ui-scenario"
    assert seen["steps"] == [{"kind": "key-entry", "path": [0, 1], "text": "hello"}]


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


# --- REA delegation ----------------------------------------------------------

REA_ENVELOPE = {
    "provider": {"id": "native-windows", "name": "Windows native UI utilities"},
    "operation": "observe_native_ui",
    "normalized_result": {
        "initial": {
            "window": {"pid": 10516, "window_id": 787822, "executable": "C:\app\App.exe", "launch_time": 1, "title": "App"},
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


def test_act_via_rea_maps_to_rea_steps(monkeypatch):
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
        FakeRea(), 787822, "set-value", "App > Text editor", "hello", pid=10516, yes=True, nodes=nodes
    )
    assert result.ok is True
    assert seen["command"] == "capture-native-ui-scenario"
    assert seen["steps"] == [{"kind": "key-entry", "path": [0, 1], "text": "hello"}]


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
