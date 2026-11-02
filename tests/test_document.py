"""Document adapter contracts; these tests never launch or drive an application."""

import importlib
import json
from pathlib import Path
import py_compile
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from auto_cli import document, document_runtime as runtime, evidence, gui, scaffold, surface


RTF = b"{\\rtf1\\ansi Release checklist\\par Client: CLIENT_NAME\\par Status: draft\\par}"


@pytest.fixture()
def generated(tmp_path):
    executable = tmp_path / "wordpad.exe"
    executable.write_bytes(b"test executable; never launched")
    bundle = evidence.EvidenceBundle(target=str(executable))
    root = scaffold.generate(surface.classify(bundle), bundle, tmp_path / "out", name="document-test-app")
    report = gui.GuiReport(process="WordPadClass", pid=1234, window="fixture.rtf - WordPad",
                           executable=str(executable))
    return root, report, executable


@pytest.fixture()
def generated_cli(generated, monkeypatch):
    root, report, executable = generated
    assert gui.upgrade_scaffold(root, report)
    assert document.upgrade_scaffold(root, report)
    prefix = "cli_anything.document_test_app"
    previous = {key: value for key, value in sys.modules.items() if key == prefix or key.startswith(prefix + ".")}
    for key in previous:
        del sys.modules[key]
    monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    try:
        yield importlib.import_module(prefix + ".document_test_app_cli"), root
    finally:
        for key in list(sys.modules):
            if key == prefix or key.startswith(prefix + "."):
                del sys.modules[key]
        sys.modules.update(previous)


@pytest.mark.parametrize("process,filename", [
    ("Notepad", "wordpad.exe"), ("WordPadClass", "notepad.exe"), ("", "wordpad.exe"),
])
def test_adapter_requires_both_recognized_class_and_executable(generated, process, filename):
    root, report, executable = generated
    assert gui.upgrade_scaffold(root, report)
    report.process = process
    report.executable = str(executable.with_name(filename))
    before = (root / "cli_anything/document_test_app/document_test_app_cli.py").read_bytes()
    assert document.upgrade_scaffold(root, report) is False
    assert (root / "cli_anything/document_test_app/document_test_app_cli.py").read_bytes() == before
    assert not (root / "cli_anything/document_test_app/utils/document_runner.py").exists()


def test_adapter_requires_gui_runtime_and_is_idempotent(generated):
    root, report, executable = generated
    assert document.upgrade_scaffold(root, report) is False
    assert gui.upgrade_scaffold(root, report)
    assert document.upgrade_scaffold(root, report)
    files = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert document.upgrade_scaffold(root, report) is False
    assert files == {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    runner = root / "cli_anything/document_test_app/utils/document_runner.py"
    py_compile.compile(str(runner), doraise=True)
    source = runner.read_text(encoding="utf-8")
    assert f"EXECUTABLE = {str(executable.resolve())!r}" in source
    assert "from auto_cli" not in source
    assert (runner.parent / "uia_runner.py").is_file()


def test_generated_package_declares_gui_dependency_and_documents_workflow(generated, monkeypatch):
    root, report, executable = generated
    assert gui.upgrade_scaffold(root, report)
    assert document.upgrade_scaffold(root, report)
    captured = {}
    monkeypatch.chdir(root)
    import setuptools
    monkeypatch.setattr(setuptools, "setup", lambda **kwargs: captured.update(kwargs))
    exec(compile((root / "setup.py").read_text(encoding="utf-8"), str(root / "setup.py"), "exec"), {})
    assert "cli_anything.document_test_app.utils" in captured["packages"]
    assert any("uiautomation" in requirement and "win32" in requirement for requirement in captured["install_requires"])
    for path in [root / "README.md", root / "cli_anything/document_test_app/README.md",
                 root / "cli_anything/document_test_app/skills/SKILL.md"]:
        text = path.read_text(encoding="utf-8")
        assert "--json workflow --input input.rtf --output approved.rtf" in text
        assert "--yes" in text and "overwrites are refused" in text
        assert "passing fixture unit tests" in text


def test_generated_document_commands_are_registered_and_have_usable_help(generated_cli):
    cli, root = generated_cli
    names = {"open", "read", "replace", "append", "bold", "save-as", "workflow"}
    assert names <= set(cli.main.commands)
    runner = CliRunner()
    for command in names:
        result = runner.invoke(cli.main, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output
    result = runner.invoke(cli.main, ["--json", "workflow"])
    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["code"] == "invalid_input"


def test_generated_workflow_wrapper_forwards_to_its_installed_runtime(generated_cli, tmp_path, monkeypatch):
    cli, root = generated_cli
    source = tmp_path / "input.rtf"
    source.write_bytes(RTF)
    output = tmp_path / "output.rtf"
    child = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"ok": true, "verification": {"reopened_in_app": true}}', stderr=""))
    monkeypatch.setattr(subprocess, "run", child)
    result = CliRunner().invoke(cli.main, ["--json", "workflow", "--input", str(source), "--output", str(output),
                                         "--old", "CLIENT_NAME", "--new", "Alex Rowan", "--text", "Approved.",
                                         "--heading", "Release checklist", "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verification"]["reopened_in_app"] is True
    argv = child.call_args.args[0]
    assert argv[:4] == [sys.executable, "-m", "cli_anything.document_test_app.utils.document_runner", "workflow"]
    assert "--new=Alex Rowan" in argv
    assert "--yes" in argv
    assert child.call_args.kwargs["encoding"] == "utf-8"
    assert not output.exists()  # The wrapper delegates; it cannot manufacture an output.


def test_document_wrapper_treats_option_like_text_as_literal_data(generated_cli, tmp_path, monkeypatch):
    cli, root = generated_cli
    session = tmp_path / "session.json"
    session.write_text("{}")
    child = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr=""))
    monkeypatch.setattr(subprocess, "run", child)
    arguments = ["--json", "replace", "--session", str(session), "--", "--yes", "--pid"]
    denied = CliRunner().invoke(cli.main, arguments)
    assert denied.exit_code == 2 and "--yes" in json.loads(denied.output)["detail"]
    child.assert_not_called()
    approved = CliRunner().invoke(cli.main, ["--json", "replace", "--session", str(session), "--yes", "--", "--yes", "--pid"])
    assert approved.exit_code == 0, approved.output
    argv = child.call_args.args[0]
    assert "--old=--yes" in argv and "--new=--pid" in argv
    assert argv.count("--yes") == 1


@pytest.mark.parametrize("child", [
    SimpleNamespace(returncode=2, stdout='{"ok": false, "detail": "changes require --yes"}', stderr=""),
    SimpleNamespace(returncode=2, stdout='{"ok": true}', stderr="failed"),
    SimpleNamespace(returncode=0, stdout='[]', stderr=""),
    SimpleNamespace(returncode=1, stdout='not JSON', stderr="crashed"),
])
def test_wrapper_failures_remain_nonzero_json(generated_cli, monkeypatch, child):
    cli, root = generated_cli
    monkeypatch.setattr(subprocess, "run", Mock(return_value=child))
    result = CliRunner().invoke(cli.main, ["--json", "read"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["ok"] is False


@pytest.fixture()
def no_gui(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a refusal must occur before application access")
    monkeypatch.setattr(runtime, "_api", forbidden)
    monkeypatch.setattr(runtime.subprocess, "Popen", forbidden)


@pytest.mark.parametrize("command", ["open", "replace", "append", "bold", "save-as", "workflow"])
def test_mutations_require_confirmation_before_accessing_app(tmp_path, no_gui, capsys, command):
    args = [command]
    if command in {"open", "workflow"}:
        args += ["--input", str(tmp_path / "missing.rtf")]
    if command == "open":
        args += ["--session", str(tmp_path / "session.json")]
    if command == "workflow":
        args += ["--output", str(tmp_path / "output.rtf")]
    assert runtime.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "--yes" in result["detail"]


@pytest.mark.parametrize("data", [b"plain text", b"{\\rtf", b"{\\rtf1", b"{\\rtf1\\ansi unclosed", b"{\\rtf1 text}}"])
def test_malformed_rtf_is_rejected_before_launch(tmp_path, no_gui, data):
    source = tmp_path / "bad.rtf"
    source.write_bytes(data)
    with pytest.raises(runtime.DocumentError, match="RTF"):
        runtime.open_document(source, tmp_path / "session.json", yes=True)
    assert not (tmp_path / "session.json").exists()
    assert source.read_bytes() == data


@pytest.mark.parametrize("data", [
    RTF,
    b"{\\rtf1\\ansi escaped \\{braces\\} and \\\\ slash}",
    b"{\\rtf1\\ansi{\\pict\\bin4 {}\x00\xff}body}",
])
def test_rtf_validation_accepts_escaped_groups_and_opaque_binary_payloads(tmp_path, data):
    source = tmp_path / "input.rtf"
    source.write_bytes(data)
    assert runtime._input(source) == source.resolve()
    assert source.read_bytes() == data


def test_rtf_validation_rejects_truncated_binary_payload():
    with pytest.raises(runtime.DocumentError, match="binary"):
        runtime._validate_rtf(b"{\\rtf1\\bin99 abc}")


def test_workflow_never_overwrites_or_creates_session_for_invalid_output(tmp_path, no_gui):
    source = tmp_path / "input.rtf"
    source.write_bytes(RTF)
    output = tmp_path / "existing.rtf"
    output.write_bytes(b"existing content")
    with pytest.raises(runtime.DocumentError, match="overwrite"):
        runtime.workflow(source, output, "CLIENT_NAME", "Alex", "Approved", "Release checklist", yes=True)
    assert output.read_bytes() == b"existing content"
    assert source.read_bytes() == RTF
    assert not (tmp_path / ".auto-cli-workflows").exists()
    with pytest.raises(runtime.DocumentError, match="extension"):
        runtime.workflow(source, tmp_path / "output.txt", "CLIENT_NAME", "Alex", "Approved", "Release checklist", yes=True)


@pytest.mark.parametrize("changes", [
    {"executable": "different.exe"}, {"closed": True}, {"pid": None}, {"pid": 0},
    {"pid": "1234"}, {"window_handle": None}, {"window_handle": 0},
])
def test_session_rejects_invalid_ownership_before_window_lookup(tmp_path, monkeypatch, changes):
    executable = str(tmp_path / "wordpad.exe")
    monkeypatch.setattr(runtime, "EXECUTABLE", executable)
    data = {"executable": executable, "closed": False, "pid": 1234, "window_handle": 9876}
    data.update(changes)
    path = tmp_path / "session.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    lookup = Mock(side_effect=AssertionError("invalid session must not resolve a live window"))
    monkeypatch.setattr(runtime, "_window", lookup)
    with pytest.raises(runtime.DocumentError):
        runtime._session(path)
    lookup.assert_not_called()


@pytest.mark.parametrize("payload", [[], None, "session"])
def test_nonobject_session_is_rejected_cleanly(tmp_path, monkeypatch, payload):
    path = tmp_path / "session.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(runtime, "_window", Mock(side_effect=AssertionError("unexpected lookup")))
    with pytest.raises(runtime.DocumentError):
        runtime._session(path)


@pytest.mark.parametrize("window", [None, SimpleNamespace(ProcessId=1234, NativeWindowHandle=1),
                                     SimpleNamespace(ProcessId=4321, NativeWindowHandle=9876)])
def test_session_requires_the_same_live_pid_and_window_handle(tmp_path, monkeypatch, window):
    executable = str(tmp_path / "wordpad.exe")
    monkeypatch.setattr(runtime, "EXECUTABLE", executable)
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"executable": executable, "pid": 1234, "window_handle": 9876}), encoding="utf-8")
    lookup = Mock(return_value=window)
    monkeypatch.setattr(runtime, "_window", lookup)
    with pytest.raises(runtime.DocumentError, match="session window"):
        runtime._session(path)
    lookup.assert_called_once_with(pid=1234)


@pytest.mark.parametrize("marker,text", [("", "one"), ("two\nlines", "two\nlines"), ("absent", "body"),
                                         ("same", "same same"), ("ana", "banana")])
def test_ambiguous_or_invalid_markers_never_mutate(marker, text, monkeypatch):
    window = object()
    document_control = Mock()
    monkeypatch.setattr(runtime, "_document", Mock(return_value=document_control))
    monkeypatch.setattr(runtime, "_read", Mock(return_value=text))
    write = Mock()
    monkeypatch.setattr(runtime, "_replace_selection", write)
    with pytest.raises(runtime.DocumentError):
        runtime.replace_text(window, marker, "new")
    write.assert_not_called()
    document_control.SetFocus.assert_not_called()


def test_already_bold_heading_does_not_toggle_it_off(monkeypatch):
    selection = Mock()
    selection.GetAttributeValue.return_value = 700
    document_control = Mock()
    monkeypatch.setattr(runtime, "_unique_range", Mock(return_value=(document_control, selection)))
    api = Mock()
    monkeypatch.setattr(runtime, "_api", lambda: api)
    assert runtime.bold_text(object(), "Release checklist") == {"heading": "Release checklist", "bold": True}
    api.resolve.assert_not_called()
    selection.Select.assert_not_called()


def test_inexact_application_selection_is_refused_before_native_write(monkeypatch):
    selected_range = Mock()
    selected_range.GetText.return_value = "unexpected selection"
    control = Mock()
    control.GetTextPattern.return_value.DocumentRange.FindText.return_value = selected_range
    monkeypatch.setattr(runtime, "_document", lambda window: control)
    monkeypatch.setattr(runtime, "_read", lambda window: "Client: CLIENT_NAME")
    write = Mock()
    monkeypatch.setattr(runtime, "_replace_selection", write)
    with pytest.raises(runtime.DocumentError, match="exact text selection"):
        runtime.replace_text(object(), "CLIENT_NAME", "Alex")
    write.assert_not_called()
    control.SetFocus.assert_not_called()


def test_native_text_replacement_refuses_other_control_classes():
    with pytest.raises(runtime.DocumentError, match="RichEdit"):
        runtime._replace_selection(SimpleNamespace(ClassName="Edit"), "text")


def test_native_replacement_rejects_nul_before_accessing_windows():
    with pytest.raises(runtime.DocumentError, match="NUL"):
        runtime._replace_selection(SimpleNamespace(ClassName="RichEdit50W"), "prefix\x00suffix")


@pytest.mark.parametrize("text,expected", [("body\r", "body"), ("body\r\r", "body\n"),
                                           ("body\r\n\r\n", "body\n"), ("", "")])
def test_normalization_preserves_additional_user_paragraphs(text, expected):
    assert runtime._normal(text) == expected


@pytest.fixture()
def workflow_state(tmp_path, monkeypatch):
    """Model app responses while retaining real session and event persistence."""
    source = tmp_path / "input.rtf"
    source.write_bytes(RTF)
    output, session_path = tmp_path / "output.rtf", tmp_path / "session.json"
    executable = str(tmp_path / "wordpad.exe")
    monkeypatch.setattr(runtime, "EXECUTABLE", executable)
    original_window = SimpleNamespace(ProcessId=101, NativeWindowHandle=1001)
    reopened_window = SimpleNamespace(ProcessId=202, NativeWindowHandle=2002)
    session = {"executable": executable, "pid": 101, "window_handle": 1001, "closed": False,
               "source": str(source), "source_sha256": runtime._digest(source)}

    def open_response(source, path, yes=False):
        assert yes
        runtime._save_session(path, session)
        runtime._event(path, "open", input=str(source), pid=101)
        return {"ok": True, "session": str(path)}

    monkeypatch.setattr(runtime, "open_document", open_response)
    monkeypatch.setattr(runtime, "_window", lambda pid=None: {101: original_window, 202: reopened_window}.get(pid))
    monkeypatch.setattr(runtime, "_unique_range", Mock(return_value=(Mock(), Mock())))
    monkeypatch.setattr(runtime, "_api", Mock(side_effect=AssertionError("unit tests must not access the application")))
    changed = "Release checklist\rClient: Alex\rStatus: draft\r"
    monkeypatch.setattr(runtime, "replace_text", Mock(return_value={"replaced": 1, "text": changed}))
    monkeypatch.setattr(runtime, "append_text", Mock(return_value={"appended": "Approved", "text": changed + "Approved\r"}))
    monkeypatch.setattr(runtime, "bold_text", Mock(return_value={"heading": "Release checklist", "bold": True}))
    save = Mock(side_effect=AssertionError("unexpected save"))
    close = Mock()
    monkeypatch.setattr(runtime, "save_as", save)
    monkeypatch.setattr(runtime, "_close_saved", close)
    monkeypatch.setattr(runtime.subprocess, "Popen", Mock(return_value=SimpleNamespace(pid=202)))
    return SimpleNamespace(source=source, output=output, session_path=session_path, save=save, close=close,
                           original_window=original_window, reopened_window=reopened_window,
                           expected="Release checklist\nClient: Alex\nStatus: draft\nApproved")


@pytest.mark.parametrize("failure,expected_actions", [("append", ["open", "replace"]),
                                                       ("bold", ["open", "replace", "append"])])
def test_workflow_failure_keeps_journal_of_completed_changes(workflow_state, monkeypatch, failure, expected_actions):
    state = workflow_state
    operation = Mock(side_effect=runtime.DocumentError(failure + " failed in the application"))
    monkeypatch.setattr(runtime, failure + "_text", operation)
    with pytest.raises(runtime.DocumentError, match=failure + " failed"):
        runtime.workflow(state.source, state.output, "CLIENT_NAME", "Alex", "Approved", "Release checklist",
                         yes=True, session_path=state.session_path)
    events = [json.loads(line) for line in state.session_path.with_name("events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["action"] for event in events] == expected_actions
    assert events[1]["result"]["replaced"] == 1
    if failure == "bold":
        assert events[2]["result"]["appended"] == "Approved"
    persisted = json.loads(state.session_path.read_text(encoding="utf-8"))
    assert persisted["closed"] is False and persisted["pid"] == state.original_window.ProcessId
    assert not state.output.exists()
    assert not state.session_path.with_name("workflow-report.json").exists()
    assert state.source.read_bytes() == RTF
    state.save.assert_not_called()
    state.close.assert_not_called()


@pytest.mark.parametrize("failure", ["text", "format"])
def test_reopen_identity_is_persisted_before_verification_can_fail(workflow_state, monkeypatch, failure):
    state = workflow_state

    def saved_response(window, output):
        # Simulate a completed application save; no application is launched.
        Path(output).write_bytes(RTF)
        return {"output": str(output), "sha256": runtime._digest(output), "save_backend": "test app response"}

    monkeypatch.setattr(runtime, "save_as", saved_response)

    def read_response(window):
        assert window is state.reopened_window
        persisted = json.loads(state.session_path.read_text(encoding="utf-8"))
        assert persisted["pid"] == window.ProcessId
        assert persisted["window_handle"] == window.NativeWindowHandle
        return ("unexpected saved text" if failure == "text" else state.expected) + "\r"

    monkeypatch.setattr(runtime, "_read", read_response)
    monkeypatch.setattr(runtime, "_bold_weight", Mock(return_value=400))
    with pytest.raises(runtime.DocumentError, match="reopened document"):
        runtime.workflow(state.source, state.output, "CLIENT_NAME", "Alex", "Approved", "Release checklist",
                         yes=True, session_path=state.session_path)
    persisted, window = runtime._session(state.session_path)
    assert window is state.reopened_window
    assert persisted["closed"] is False
    events = [json.loads(line) for line in state.session_path.with_name("events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["action"] for event in events] == ["open", "replace", "append", "bold", "save-as", "reopen"]
    assert events[-1]["pid"] == state.reopened_window.ProcessId
    assert events[-1]["window_handle"] == state.reopened_window.NativeWindowHandle
    state.close.assert_called_once_with(state.original_window)
    assert not state.session_path.with_name("workflow-report.json").exists()


def test_runner_parse_errors_and_missing_target_are_json(monkeypatch, capsys):
    assert runtime.main(["unknown-command"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    monkeypatch.setattr(runtime, "_window", lambda **kwargs: None)
    assert runtime.main(["read"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and "not found" in result["detail"]
