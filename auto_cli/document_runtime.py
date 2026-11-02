"""WordPad document operations, copied into a generated harness's utils package.

All edits happen in WordPad on a private copy. RTF is never rewritten offline.
Only a session launched by this module may be mutated; successful workflows
save through the application's dialog and verify a newly opened document.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

EXECUTABLE = ""
PROCESS_CLASS = "WordPadClass"


class DocumentError(Exception):
    pass


def _api():
    from . import uia_runner
    return uia_runner


def _normal(text):
    normalized = _lines(text)
    # RichEdit exposes one implicit final paragraph delimiter. Preserve any
    # additional trailing paragraphs belonging to the user's document.
    return normalized[:-1] if normalized.endswith("\n") else normalized


def _lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _wait(call, description, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = call()
        if result:
            return result
        time.sleep(0.1)
    raise DocumentError("timed out waiting for " + description)


def _window(pid=None, title=None):
    return _api().find_window(process=PROCESS_CLASS, title=title, pid=pid, executable=EXECUTABLE)


def _document(window):
    node = _api().resolve(window, "Rich Text Window")
    if node is None:
        raise DocumentError("WordPad's document control was not found")
    return node


def _read(window):
    return _api().read_text(_document(window))


def _save_session(path, session):
    Path(path).write_text(json.dumps(session, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _session(path):
    if not path or not Path(path).is_file():
        raise DocumentError("session not found; first run open INPUT --session FILE --yes")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("executable") != EXECUTABLE or data.get("closed"):
        raise DocumentError("session is closed or belongs to a different executable")
    if not isinstance(data.get("pid"), int) or data["pid"] <= 0 or not data.get("window_handle"):
        raise DocumentError("session has no valid owned process/window identity")
    window = _window(pid=data.get("pid"))
    if window is None or window.ProcessId != data["pid"] or window.NativeWindowHandle != data.get("window_handle"):
        raise DocumentError("session window no longer exists; open a fresh session")
    return data, window


def _event(path, action, **values):
    record = {"action": action, "session": str(Path(path).resolve()), "time": time.time(), **values}
    with Path(path).with_name("events.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=True) + "\n")


def _require_yes(yes):
    if not yes:
        raise DocumentError("document changes require --yes")


def _validate_rtf(data):
    """Reject broken group structure before launching; honor RTF binary payloads."""
    if not re.match(rb"\{\\rtf\d+\b", data):
        raise DocumentError("input must be an existing RTF document")
    depth = 0
    index = 0
    while index < len(data):
        value = data[index]
        index += 1
        if value == 92:  # escaped brace, symbol or control word
            match = re.match(rb"([a-zA-Z]+)(-?\d+)? ?", data[index:])
            if match:
                index += match.end()
                if match[1] == b"bin":
                    size = int(match[2] or b"-1")
                    if size < 0 or index + size > len(data):
                        raise DocumentError("invalid RTF binary payload")
                    index += size
            elif index < len(data):
                index += 1
        elif value == 123:
            depth += 1
        elif value == 125:
            depth -= 1
            if depth < 0:
                raise DocumentError("unbalanced RTF groups")
            if depth == 0:
                if data[index:].strip(b" \t\r\n\x00"):
                    raise DocumentError("unexpected content after the RTF document")
                return
    raise DocumentError("incomplete RTF document")


def _input(path):
    source = Path(path).resolve()
    if not source.is_file():
        raise DocumentError("input must be an existing RTF document")
    _validate_rtf(source.read_bytes())
    return source


def _output(path, source=None):
    target = Path(path).resolve()
    if target.suffix.lower() != ".rtf":
        raise DocumentError("output must have an .rtf extension")
    if target.exists() or (source and target == Path(source).resolve()):
        raise DocumentError("output already exists; choose a new path (overwrite is refused)")
    if not target.parent.is_dir():
        raise DocumentError("output parent directory does not exist")
    return target


def open_document(source, session_path, yes=False):
    _require_yes(yes)
    source = _input(source)
    session_path = Path(session_path).resolve()
    if session_path.exists():
        raise DocumentError("session file already exists; choose a new session path")
    if not session_path.parent.is_dir():
        raise DocumentError("session parent directory does not exist")
    executable = Path(EXECUTABLE)
    if not executable.is_file():
        raise DocumentError("WordPad executable not found: " + EXECUTABLE)
    # Back up the entire original RTF, including all formatting, before editing.
    workspace = Path(tempfile.mkdtemp(prefix="wordpad-", dir=session_path.parent))
    working = workspace / "working.rtf"
    shutil.copy2(source, working)
    proc = subprocess.Popen([EXECUTABLE, str(working)])
    session = {"executable": EXECUTABLE, "pid": proc.pid, "source": str(source),
               "source_sha256": _digest(source), "working_copy": str(working), "closed": False}
    _save_session(session_path, session)
    window = _wait(lambda: _window(pid=proc.pid), "owned WordPad window")
    session["window_handle"] = window.NativeWindowHandle
    _save_session(session_path, session)
    text = _read(window)
    _event(session_path, "open", input=str(source), working_copy=str(working), pid=proc.pid)
    return {"ok": True, "session": str(session_path), "pid": proc.pid,
            "working_copy": str(working), "text": text}


def _replace_selection(document, text):
    """Use RichEdit's native replacement message; preserves surrounding RTF.

    EM_REPLACESEL is below WM_USER, so Windows marshals its Unicode buffer
    between processes. No remote allocation or target memory writes are used.
    """
    from ctypes import wintypes
    if "\x00" in text:
        raise DocumentError("replacement text must not contain NUL characters")
    if not (document.ClassName or "").lower().startswith("richedit"):
        raise DocumentError("document is not a supported native RichEdit control")
    user32 = ctypes.windll.user32
    send = user32.SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                     wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                     ctypes.POINTER(ctypes.c_size_t)]
    send.restype = wintypes.LPARAM
    buffer = ctypes.create_unicode_buffer(text)
    result = ctypes.c_size_t()
    if not send(document.NativeWindowHandle, 0x00C2, 1, ctypes.addressof(buffer),
                0x0002, 5000, ctypes.byref(result)):
        raise DocumentError("WordPad did not accept the text replacement")


def _unique_range(window, text):
    if not text or "\r" in text or "\n" in text:
        raise DocumentError("selection must be nonempty text within one paragraph")
    document = _document(window)
    content = _read(window)
    if content.find(text) < 0 or content.find(text) != content.rfind(text):
        raise DocumentError("selection must occur exactly once in the document")
    found = document.GetTextPattern().DocumentRange.FindText(text, False, False)
    if found is None or found.GetText(-1) != text:
        raise DocumentError("WordPad could not resolve the exact text selection")
    return document, found


def replace_text(window, old, new):
    before = _read(window)
    document, selection = _unique_range(window, old)
    document.SetFocus()
    selection.Select()
    _replace_selection(document, new)
    expected = _normal(before.replace(old, new, 1))
    _wait(lambda: _normal(_read(window)) == expected, "replacement readback")
    return {"replaced": 1, "text": _read(window)}


def append_text(window, text):
    if not text:
        raise DocumentError("append text must not be empty")
    before = _normal(_read(window))
    document = _document(window)
    end = document.GetTextPattern().DocumentRange.Clone()
    end.MoveEndpointByRange(0, end, 1)  # collapse start to end
    document.SetFocus()
    end.Select()
    _replace_selection(document, ("\r" if before else "") + text)
    expected = before + ("\n" if before else "") + _lines(text)
    _wait(lambda: _normal(_read(window)) == expected, "appended text readback")
    return {"appended": text, "text": _read(window)}


def _bold_weight(selection):
    value = selection.GetAttributeValue(40007)  # UIA_FontWeightAttributeId
    if not isinstance(value, (int, float)):
        raise DocumentError("WordPad did not expose a uniform font weight for the selection")
    return value


def bold_text(window, heading):
    document, selection = _unique_range(window, heading)
    if _bold_weight(selection) < 700:
        document.SetFocus()
        selection.Select()
        button = _api().resolve(window, "Bold")
        if button is None:
            raise DocumentError("WordPad Bold control was not found")
        button.GetInvokePattern().Invoke()
    _wait(lambda: _bold_weight(_unique_range(window, heading)[1]) >= 700, "bold formatting")
    return {"heading": heading, "bold": True}


def _descendants(control, depth=0):
    if depth > 12:
        return
    for child in control.GetChildren():
        yield child
        yield from _descendants(child, depth + 1)


def _save_dialog(window):
    candidates = list(_descendants(window))
    candidates += list(_api().auto.GetRootControl().GetChildren())
    matches = [node for node in candidates if node.ProcessId == window.ProcessId
               and node.ClassName == "#32770" and node.Name == "Save As"]
    unique = {node.NativeWindowHandle: node for node in matches}
    if len(unique) > 1:
        raise DocumentError("multiple Save As dialogs match the owned session")
    return next(iter(unique.values()), None)


def _open_save_dialog(window):
    pending = _save_dialog(window)
    if pending is not None:
        return pending
    groups = [node for node in _descendants(window)
              if node.Name == "Save as" and node.ControlTypeName == "GroupControl"]
    if not groups:
        tab = _api().resolve(window, "File tab")
        if tab is None:
            raise DocumentError("WordPad File tab was not found")
        tab.GetInvokePattern().Invoke()
        groups = _wait(lambda: [node for node in _descendants(window)
                               if node.Name == "Save as" and node.ControlTypeName == "GroupControl"],
                       "WordPad Save as menu")
    if len(groups) != 1:
        raise DocumentError("WordPad Save as menu is ambiguous")
    buttons = [node for node in groups[0].GetChildren()
               if node.Name == "Save as" and node.ControlTypeName == "SplitButtonControl"]
    # WordPad exposes the split button's main area and dropdown arrow as
    # equally named siblings. The wider main area invokes Save As directly.
    widths = [(node.BoundingRectangle.right - node.BoundingRectangle.left, node) for node in buttons]
    if not widths:
        raise DocumentError("WordPad Save as main control was not found")
    widest = max(width for width, _ in widths)
    primary = [node for width, node in widths if width == widest]
    if len(primary) != 1:
        raise DocumentError("WordPad Save as main control is ambiguous")
    primary[0].GetInvokePattern().Invoke()
    return _wait(lambda: _save_dialog(window), "WordPad Save As dialog")


def save_as(window, path):
    output = _output(path)
    dialog = _open_save_dialog(window)
    nodes = list(_descendants(dialog))
    fields = [node for node in nodes if node.ControlTypeName == "EditControl"
              and node.Name == "File name:"]
    buttons = [node for node in nodes if node.ControlTypeName == "ButtonControl"
               and node.Name == "Save"]
    if len(fields) != 1 or len(buttons) != 1:
        raise DocumentError("unsupported Save As dialog; expected one filename field and Save button")
    fields[0].GetValuePattern().SetValue(str(output))
    _output(output)  # recheck immediately before the app performs the write
    buttons[0].GetInvokePattern().Invoke()
    _wait(lambda: output.is_file() and output.stat().st_size > 0
          and output.name.lower() in (window.Name or "").lower(), "saved RTF output")
    if not output.read_bytes().startswith(b"{\\rtf"):
        raise DocumentError("WordPad output is not an RTF document")
    return {"output": str(output), "sha256": _digest(output), "save_backend": "WordPad Save As dialog"}


def _close_saved(window):
    pid = window.ProcessId
    window.GetWindowPattern().Close()
    _wait(lambda: _window(pid=pid) is None, "saved WordPad window to close")


def workflow(source, output, old, new, text, heading, yes=False, session_path=None):
    _require_yes(yes)
    source = _input(source)
    output = _output(output, source)
    if not old or not text or not heading:
        raise DocumentError("workflow requires nonempty --old, --text and --heading")
    if session_path is None:
        logs = output.parent / ".auto-cli-workflows"
        logs.mkdir(exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix="run-", dir=logs))
        session_path = run / "session.json"
    session_path = Path(session_path).resolve()
    opened = open_document(source, session_path, yes=True)
    session, window = _session(session_path)
    # Reject missing/ambiguous markers before any document mutation.
    _unique_range(window, old)
    _unique_range(window, heading)
    changed = replace_text(window, old, new)
    _event(session_path, "replace", result=changed)
    after_replace = _normal(changed["text"])
    for name, operation in (("append", lambda: append_text(window, text)),
                            ("bold", lambda: bold_text(window, heading))):
        result = operation()
        _event(session_path, name, result=result)
    expected = after_replace + ("\n" if after_replace else "") + _lines(text)
    saved = save_as(window, output)
    _event(session_path, "save-as", **saved)
    _close_saved(window)
    proc = subprocess.Popen([EXECUTABLE, str(output)])
    reopened = _wait(lambda: _window(pid=proc.pid), "reopened saved WordPad document")
    session.update(pid=proc.pid, window_handle=reopened.NativeWindowHandle)
    _save_session(session_path, session)
    _event(session_path, "reopen", pid=proc.pid, window_handle=reopened.NativeWindowHandle)
    actual = _normal(_read(reopened))
    if actual != expected or _bold_weight(_unique_range(reopened, heading)[1]) < 700:
        raise DocumentError("reopened document did not match expected text and bold heading")
    if _digest(source) != session["source_sha256"]:
        raise DocumentError("original input changed during the workflow")
    result = {"ok": True, **saved, "session": str(session_path), "text": actual,
              "verification": {"reopened_in_app": True, "text_matches": True,
                               "heading_bold": True, "source_unchanged": True},
              "steps": ["open-copy", "replace", "append", "bold", "save-as", "reopen", "verify"]}
    _event(session_path, "verified", **result["verification"])
    _close_saved(reopened)
    session.update(closed=True, output=str(output))
    _save_session(session_path, session)
    session_path.with_name("workflow-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DocumentError(message)


def main(argv=None):
    parser = _Parser()
    parser.add_argument("command", choices=["open", "read", "replace", "append", "bold", "save-as", "workflow"])
    for name in ("input", "output", "old", "new", "text", "heading", "session", "title"):
        parser.add_argument("--" + name)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--yes", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.command == "workflow":
            if not args.input or not args.output:
                raise DocumentError("workflow requires --input and --output")
            result = workflow(args.input, args.output, args.old, args.new or "", args.text,
                              args.heading, args.yes, args.session)
        elif args.command == "open":
            if not args.input or not args.session:
                raise DocumentError("open requires --input and --session")
            result = open_document(args.input, args.session, args.yes)
        elif args.command == "read" and not args.session:
            window = _window(pid=args.pid, title=args.title)
            if window is None:
                raise DocumentError("target window not found")
            result = {"ok": True, "pid": window.ProcessId, "text": _read(window)}
        else:
            if args.command != "read":
                _require_yes(args.yes)
            session, window = _session(args.session)
            if args.command == "read":
                result = {"text": _read(window)}
            elif args.command == "replace":
                result = replace_text(window, args.old, args.new or "")
            elif args.command == "append":
                result = append_text(window, args.text)
            elif args.command == "bold":
                result = bold_text(window, args.heading)
            else:
                if not args.output:
                    raise DocumentError("save-as requires --output")
                result = save_as(window, args.output)
                session["output"] = result["output"]
                _save_session(args.session, session)
            _event(args.session, args.command, result=result)
            result = {"ok": True, **result}
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
