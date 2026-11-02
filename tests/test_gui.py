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
    assert "def ui_read_text" in cli
    assert "def ui_invoke" in cli
    assert "TARGET_WINDOW_PROCESS" in (root / "cli_anything" / "gui_app" / "core" / "backend.py").read_text()
    assert "TARGET_WINDOW_PID = 1234" in (root / "cli_anything" / "gui_app" / "core" / "backend.py").read_text()
    assert "uiautomation>=2.0.20; sys_platform == 'win32'" in (root / "setup.py").read_text()
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
    assert 'ui-read-text "<path>"' in skill


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
        find=lambda pid=None, process=None, title_contains=None, executable=None: runner.find_window(process, title_contains, pid, executable),
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


def test_window_selection_tracks_restart_and_title_changes_with_executable_scope(driver, monkeypatch):
    paths = {1: "C:/Tools/App.exe", 2: "C:/Other/App.exe", 3: "c:\\tools\\app.exe"}
    monkeypatch.setattr(driver.module, "window_executable", lambda pid: paths.get(pid, ""))
    original = FakeControl("Untitled", pid=1)
    unrelated = FakeControl("Untitled", pid=2)
    driver.root.children = [original, unrelated]
    assert driver.find(process="App", executable="C:/Tools/App.exe") is original
    restarted = FakeControl("Saved document - App", pid=3)
    driver.root.children = [unrelated, restarted]
    assert driver.find(process="App", executable="C:/Tools/App.exe") is restarted
    assert driver.find(process="App", executable="C:/Tools/App.exe", pid=1) is None
    assert driver.find(process="App", executable="C:/Tools/App.exe", title_contains="Untitled") is None


def test_executable_scope_fails_closed_if_identity_cannot_be_read(driver, monkeypatch):
    monkeypatch.setattr(driver.module, "window_executable", lambda pid: "")
    driver.root.children = [FakeControl("Window")]
    assert driver.find(process="App", executable="C:/Tools/App.exe") is None


def test_executable_scope_still_requires_explicit_selector_for_multiple_documents(driver, monkeypatch):
    monkeypatch.setattr(driver.module, "window_executable", lambda pid: "C:/Tools/App.exe")
    first = FakeControl("First", pid=1)
    second = FakeControl("Second", pid=2)
    driver.root.children = [first, second]
    with pytest.raises(driver.error, match="multiple windows"):
        driver.find(process="App", executable="C:/Tools/App.exe")
    assert driver.find(process="App", executable="C:/Tools/App.exe", pid=2) is second


def test_capture_and_resolve_deep_document_controls_and_text_pattern(driver):
    document = FakeControl("Rich document", control_type="DocumentControl")
    document.GetInvokePattern = lambda: None
    document.GetTextPattern = lambda: types.SimpleNamespace()
    control = document
    for level in range(9):
        control = FakeControl(f"Pane {level}", children=[control])
    window = FakeControl("Window", children=[control])
    nodes = driver.capture(window)
    captured = next(node for node in nodes if (node.name if isinstance(node, gui_mod.UiNode) else node["name"]) == "Rich document")
    patterns = captured.patterns if isinstance(captured, gui_mod.UiNode) else captured["patterns"]
    assert "Text" in patterns
    assert driver.resolve(window, "Rich document") is document


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


@pytest.fixture()
def generated_runner(monkeypatch):
    root = FakeControl()
    fake_auto = types.SimpleNamespace(GetRootControl=lambda: root)
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    runner = types.ModuleType("read_text_uia_runner")
    exec(compile(gui_mod.UIA_RUNNER_PY, "uia_runner.py", "exec"), runner.__dict__)
    return types.SimpleNamespace(root=root, module=runner)


def _run_generated(runner, monkeypatch, capsys, command, path, *flags):
    literal = [command, path]
    if command == "set-value" and flags:
        literal += [flags[0]]
        flags = flags[1:]
    monkeypatch.setattr(sys, "argv", ["uia_runner.py", "--process=App", *flags, "--", *literal])
    status = runner.module.main()
    return status, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("text", ["", "Release notes\nFixed export – ready to ship."])
def test_generated_runner_reads_value_without_confirmation(generated_runner, monkeypatch, capsys, text):
    editor = FakeControl("Save preview")
    editor.GetValuePattern = Mock(return_value=types.SimpleNamespace(Value=text))
    generated_runner.root.children = [FakeControl("Window", children=[editor])]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "read-text", "Save preview")
    assert status == 0
    assert payload == {"ok": True, "path": "Save preview", "text": text}
    editor.invoke.assert_not_called()


@pytest.mark.parametrize("value_error", [False, True])
def test_generated_runner_reads_document_text(generated_runner, monkeypatch, capsys, value_error):
    editor = FakeControl("Text editor")
    if value_error:
        editor.GetValuePattern = Mock(side_effect=RuntimeError("Value pattern unavailable"))
    get_text = Mock(return_value="Actual document contents\n")
    editor.GetTextPattern = Mock(return_value=types.SimpleNamespace(DocumentRange=types.SimpleNamespace(GetText=get_text)))
    generated_runner.root.children = [FakeControl("Window", children=[editor])]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "read-text", "Text editor")
    assert status == 0
    assert payload["text"] == "Actual document contents\n"
    get_text.assert_called_once_with(-1)
    editor.invoke.assert_not_called()


def test_generated_runner_read_text_reports_unsupported_control(generated_runner, monkeypatch, capsys):
    generated_runner.root.children = [FakeControl("Window", children=[FakeControl("Button")])]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "read-text", "Button")
    assert status == 2
    assert payload["ok"] is False
    assert "readable Value or Text" in payload["detail"]
    assert "text" not in payload


def test_generated_runner_read_text_reports_stale_control(generated_runner, monkeypatch, capsys):
    editor = FakeControl("Text editor")
    editor.GetValuePattern = Mock(side_effect=RuntimeError("element is stale"))
    editor.GetTextPattern = Mock(side_effect=RuntimeError("element is stale"))
    generated_runner.root.children = [FakeControl("Window", children=[editor])]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "read-text", "Text editor")
    assert status == 2
    assert payload["ok"] is False
    assert "element is stale" in payload["detail"]


def test_generated_runner_read_text_refuses_ambiguous_path(generated_runner, monkeypatch, capsys):
    editors = [FakeControl("Text editor"), FakeControl("Text editor")]
    for editor in editors:
        editor.GetValuePattern = Mock()
    generated_runner.root.children = [FakeControl("Window", children=editors)]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "read-text", "Text editor")
    assert status == 2
    assert "ambiguous node" in payload["detail"]
    for editor in editors:
        editor.GetValuePattern.assert_not_called()


@pytest.mark.parametrize("command", ["invoke", "select", "set-value"])
def test_generated_runner_mutations_still_require_confirmation(generated_runner, monkeypatch, capsys, command):
    target = FakeControl("Text editor")
    generated_runner.root.children = [FakeControl("Window", children=[target])]
    args = ("replacement",) if command == "set-value" else ()
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, command, "Text editor", *args)
    assert status == 2
    assert "--yes" in payload["detail"]
    target.invoke.assert_not_called()


def test_generated_runner_mutations_still_refuse_destructive_paths(generated_runner, monkeypatch, capsys):
    target = FakeControl("Save")
    generated_runner.root.children = [FakeControl("Window", children=[target])]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "invoke", "Save", "--yes")
    assert status == 2
    assert "destructive" in payload["detail"]
    target.invoke.assert_not_called()


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
    monkeypatch.setitem(namespace, "_uia_run", lambda args, **options: {"ok": False, "detail": "target window not found"})
    result = CliRunner().invoke(main, ["ui-tree"])
    assert result.exit_code == 2
    assert json.loads(result.output)["ok"] is False


def test_generated_ui_read_text_forwards_without_confirmation(monkeypatch):
    import click
    from click.testing import CliRunner

    @click.group()
    def main():
        pass

    namespace = {"click": click, "main": main, "_emit": lambda ctx, payload: click.echo(json.dumps(payload))}
    exec(gui_mod.UI_CLI_TEMPLATE, namespace)
    run = Mock(return_value={"ok": True, "path": "Text editor", "text": "Release notes"})
    monkeypatch.setitem(namespace, "_uia_run", run)
    result = CliRunner().invoke(main, ["ui-read-text", "Text editor"])
    assert result.exit_code == 0
    assert json.loads(result.output)["text"] == "Release notes"
    run.assert_called_once_with(["read-text", "Text editor"], pid=None, title=None)


@pytest.mark.parametrize("arguments", [
    ["ui-info"], ["ui-tree"], ["ui-menus"], ["ui-read-text", "Editor"],
    ["ui-invoke", "Button", "--yes", "--allow-destructive"],
    ["ui-select", "Item", "--yes"], ["ui-set-text", "Editor", "new text", "--yes"],
])
def test_every_public_ui_command_accepts_current_target_overrides(monkeypatch, arguments):
    import click
    from click.testing import CliRunner

    @click.group()
    def main():
        pass

    namespace = {"click": click, "main": main, "_emit": lambda ctx, payload: click.echo(json.dumps(payload))}
    exec(gui_mod.UI_CLI_TEMPLATE, namespace)
    run = Mock(return_value={"ok": True})
    monkeypatch.setitem(namespace, "_uia_run", run)
    result = CliRunner().invoke(main, [*arguments, "--pid", "4321", "--title", "Current document"])
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["pid"] == 4321
    assert run.call_args.kwargs["title"] == "Current document"
    if "--allow-destructive" in arguments:
        assert run.call_args.kwargs["allow_destructive"] is True
    if arguments[0] in {"ui-tree", "ui-menus"}:
        assert run.call_args.kwargs["max_depth"] == 12
        assert run.call_args.kwargs["max_nodes"] == 400


def test_generated_runner_denied_mutation_and_positive_control(generated_runner, monkeypatch, capsys):
    editor = FakeControl("Editor")
    setter = Mock()
    editor.GetValuePattern = lambda: types.SimpleNamespace(SetValue=setter)
    generated_runner.root.children = [FakeControl("Window", children=[editor])]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "set-value", "Editor", "new text")
    assert status == 2
    assert payload["error"] == "approval_required"
    setter.assert_not_called()
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "set-value", "Editor", "new text", "--yes")
    assert status == 0
    setter.assert_called_once_with("new text")


def test_generated_runner_destructive_override_reaches_live_control(generated_runner, monkeypatch, capsys):
    button = FakeControl("Save")
    generated_runner.root.children = [FakeControl("Window", children=[button])]
    status, payload = _run_generated(generated_runner, monkeypatch, capsys, "invoke", "Save", "--yes", "--allow-destructive")
    assert status == 0
    button.invoke.assert_called_once_with()


def test_generated_runner_invalid_bounds_and_syntax_emit_json(generated_runner, monkeypatch, capsys):
    for arguments in (["tree", "--max-depth", "0"], ["tree", "--max-nodes", "10001"], ["no-such-command"]):
        monkeypatch.setattr(sys, "argv", ["uia_runner.py", *arguments])
        assert generated_runner.module.main() == 2
        assert json.loads(capsys.readouterr().out)["error"] == "invalid_input"


@pytest.fixture()
def public_gui_runner(generated_runner, tmp_path, monkeypatch):
    """Exercise public Click commands through their actual child argv parser."""
    import contextlib
    import io
    import click
    from click.testing import CliRunner

    @click.group()
    def main():
        pass

    namespace = {
        "click": click, "main": main, "json": json,
        "__file__": str(tmp_path / "app_cli.py"),
        "backend": types.SimpleNamespace(TARGET_WINDOW_PROCESS="App", TARGET_WINDOW_EXECUTABLE="C:/App.exe"),
        "_emit": lambda ctx, payload: click.echo(json.dumps(payload)),
    }
    exec(gui_mod.UI_CLI_TEMPLATE, namespace)
    monkeypatch.setattr(generated_runner.module, "window_executable", lambda pid: "C:/App.exe")
    calls = []

    def child_run(argv, **kwargs):
        calls.append(argv)
        output = io.StringIO()
        with monkeypatch.context() as context:
            context.setattr(sys, "argv", [argv[1], *argv[2:]])
            with contextlib.redirect_stdout(output):
                status = generated_runner.module.main()
        return types.SimpleNamespace(returncode=status, stdout=output.getvalue(), stderr="")

    monkeypatch.setattr(namespace["_sp"], "run", child_run)
    return types.SimpleNamespace(root=generated_runner.root, cli=main, runner=CliRunner(), calls=calls)


@pytest.mark.parametrize("path,text", [("Editor", "--yes"), ("--yes", "replacement"), ("--pid", "--yes")])
def test_public_gui_literal_options_cannot_supply_mutation_consent(public_gui_runner, path, text):
    setter = Mock()
    control = FakeControl(path)
    control.GetValuePattern = lambda: types.SimpleNamespace(SetValue=setter)
    public_gui_runner.root.children = [FakeControl("Window", children=[control])]
    result = public_gui_runner.runner.invoke(public_gui_runner.cli, ["ui-set-text", "--", path, text])
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "approval_required"
    assert public_gui_runner.calls == []
    setter.assert_not_called()


@pytest.mark.parametrize("path,text", [
    ("--yes", "--pid=999"), ("--pid", "--allow-destructive"),
    ("Éditeur 東京", "--title\nRésumé – approved ✓"),
])
def test_public_gui_optionlike_and_unicode_literals_round_trip_unchanged(public_gui_runner, path, text):
    setter = Mock()
    control = FakeControl(path)
    control.GetValuePattern = lambda: types.SimpleNamespace(SetValue=setter)
    public_gui_runner.root.children = [FakeControl("--pid document", children=[control], pid=777)]
    result = public_gui_runner.runner.invoke(public_gui_runner.cli, [
        "ui-set-text", "--yes", "--pid", "777", "--title=--pid", "--", path, text,
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    setter.assert_called_once_with(text)
    argv = public_gui_runner.calls[0]
    sentinel = argv.index("--")
    assert argv[sentinel + 1:] == ["set-value", path, text]
    assert "--yes" in argv[:sentinel]
    assert "--pid=777" in argv[:sentinel]
    assert "--title=--pid" in argv[:sentinel]
    assert "--pid=999" not in argv[:sentinel]


def test_public_gui_literal_override_does_not_disable_destructive_guard(public_gui_runner):
    setter = Mock()
    control = FakeControl("Save")
    control.GetValuePattern = lambda: types.SimpleNamespace(SetValue=setter)
    public_gui_runner.root.children = [FakeControl("Window", children=[control])]
    result = public_gui_runner.runner.invoke(public_gui_runner.cli, ["ui-set-text", "--yes", "Save", "--", "--allow-destructive"])
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "destructive_path"
    setter.assert_not_called()


def test_public_gui_tree_limits_are_options_before_literal_sentinel(public_gui_runner):
    public_gui_runner.root.children = [FakeControl("Window")]
    result = public_gui_runner.runner.invoke(public_gui_runner.cli, ["ui-tree", "--max-depth", "20", "--max-nodes", "750"])
    assert result.exit_code == 0, result.output
    argv = public_gui_runner.calls[0]
    sentinel = argv.index("--")
    assert "--max-depth=20" in argv[:sentinel]
    assert "--max-nodes=750" in argv[:sentinel]
    assert argv[sentinel + 1:] == ["tree"]


def test_generated_ui_runner_uses_stable_identity_instead_of_captured_pid_and_title(tmp_path, monkeypatch):
    import click

    @click.group()
    def main():
        pass

    namespace = {
        "click": click, "main": main, "json": json,
        "__file__": str(tmp_path / "notes_cli.py"),
        "backend": types.SimpleNamespace(
            TARGET_WINDOW_PROCESS="Notepad", TARGET_WINDOW_TITLE="Untitled - Notepad", TARGET_WINDOW_PID=1234,
            TARGET_WINDOW_EXECUTABLE="C:/Windows/notepad.exe",
        ),
    }
    exec(gui_mod.UI_CLI_TEMPLATE, namespace)
    run = Mock(return_value=types.SimpleNamespace(stdout='{"ok": true}', stderr=""))
    monkeypatch.setattr(namespace["_sp"], "run", run)
    assert namespace["_uia_run"](["tree"]) == {"ok": True}
    args = run.call_args.args[0]
    assert "--process=Notepad" in args[:args.index("--")]
    assert "--executable=C:/Windows/notepad.exe" in args[:args.index("--")]
    assert args[args.index("--") + 1:] == ["tree"]
    assert "--pid" not in args
    assert "--title" not in args


def test_generated_ui_runner_without_captured_executable_requires_explicit_target(tmp_path, monkeypatch):
    import click

    @click.group()
    def main():
        pass

    namespace = {
        "click": click, "main": main, "json": json,
        "__file__": str(tmp_path / "notes_cli.py"),
        "backend": types.SimpleNamespace(TARGET_WINDOW_PROCESS="App", TARGET_WINDOW_PID=1234),
    }
    exec(gui_mod.UI_CLI_TEMPLATE, namespace)
    run = Mock(return_value=types.SimpleNamespace(stdout='{"ok": true}', stderr=""))
    monkeypatch.setattr(namespace["_sp"], "run", run)
    assert namespace["_uia_run"](["tree"])["error"] == "target_identity_missing"
    run.assert_not_called()
    assert namespace["_uia_run"](["tree"], pid=4321) == {"ok": True}
    args = run.call_args.args[0]
    assert "--pid=4321" in args[:args.index("--")]


def test_generated_runner_import_without_gui_dependency_does_not_exit(monkeypatch):
    monkeypatch.setitem(sys.modules, "uiautomation", None)
    namespace = {"__name__": "runner_without_uia"}
    exec(compile(gui_mod.UIA_RUNNER_PY, "uia_runner.py", "exec"), namespace)
    with pytest.raises(ValueError, match="uiautomation unavailable"):
        namespace["find_window"](process="App")


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
