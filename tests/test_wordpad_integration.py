"""Opt-in, interactive Windows test of a freshly generated and installed CLI.

Set AUTO_CLI_WORDPAD=1, REA_BIN and optionally AUTO_CLI_WORDPAD_EXE.
Requires an English WordPad installation and no existing WordPad windows.
This test uses only its own fixtures and never downloads dependencies.
"""

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import site
import subprocess
import sys
import time
import uuid

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("AUTO_CLI_WORDPAD") != "1" or sys.platform != "win32",
    reason="set AUTO_CLI_WORDPAD=1 on interactive Windows with English WordPad and installed REA",
)


def _interactive_desktop():
    from ctypes import wintypes

    class Flags(ctypes.Structure):
        _fields_ = [("inherited", wintypes.BOOL), ("reserved", wintypes.BOOL), ("flags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    query = user32.GetUserObjectInformationW
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                      ctypes.POINTER(wintypes.DWORD)]
    flags, needed = Flags(), wintypes.DWORD()
    return bool(query(user32.GetProcessWindowStation(), 1, ctypes.byref(flags),
                      ctypes.sizeof(flags), ctypes.byref(needed)) and flags.flags & 1)


def _run(root, label, argv, *, cwd, env, expected=0, timeout=240):
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        (root / f"{label}.timeout.txt").write_text(str(exc), encoding="utf-8")
        raise
    (root / f"{label}.stdout.log").write_text(result.stdout, encoding="utf-8")
    (root / f"{label}.stderr.log").write_text(result.stderr, encoding="utf-8")
    (root / f"{label}.command.json").write_text(json.dumps({
        "argv": [str(value) for value in argv], "cwd": str(cwd),
        "exit_code": result.returncode, "elapsed_seconds": time.monotonic() - started,
    }, indent=2), encoding="utf-8")
    if expected is not None:
        assert result.returncode == expected, (
            f"{label}: exit {result.returncode}; evidence: {root}\n{result.stdout}\n{result.stderr}"
        )
    return result


def _payload(result):
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), result.stdout
    return payload


def _rtf_characters(data):
    """Independent reader for visible text/bold in the ASCII test fixtures.

    Handles WordPad's groups, destinations, escaped text and basic character
    controls. This is an output assertion helper, never an editing backend.
    """
    state = {"skip": False, "bold": False, "uc": 1}
    stack, characters, weights = [], [], []
    skip_fallback = 0
    destinations = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object",
                    "generator", "header", "footer", "listtable", "listoverridetable", "fldinst"}

    def emit(text):
        nonlocal skip_fallback
        for character in text:
            if skip_fallback:
                skip_fallback -= 1
            elif not state["skip"]:
                characters.append(character)
                weights.append(state["bold"])

    index = 0
    while index < len(data):
        value = data[index]
        index += 1
        if value == 123:
            stack.append(dict(state))
        elif value == 125:
            state = stack.pop()
        elif value == 92:
            if index >= len(data):
                break
            symbol = data[index]
            if symbol in (92, 123, 125):
                emit(chr(symbol))
                index += 1
            elif symbol == 42:
                state["skip"] = True
                index += 1
            elif symbol == 39:
                emit(bytes.fromhex(data[index + 1:index + 3].decode("ascii")).decode("cp1252"))
                index += 3
            else:
                match = re.match(rb"([a-zA-Z]+)(-?\d+)? ?", data[index:])
                if not match:
                    index += 1
                    continue
                index += match.end()
                word = match[1].decode("ascii")
                number = int(match[2]) if match[2] else None
                if word in destinations:
                    state["skip"] = True
                elif word == "bin":
                    index += number or 0
                elif word == "b":
                    state["bold"] = number != 0
                elif word == "plain":
                    state["bold"] = False
                elif word == "uc":
                    state["uc"] = number
                elif word == "u" and number is not None:
                    emit(chr(number % 65536))
                    skip_fallback = state["uc"]
                elif word in {"par", "line"}:
                    emit("\n")
                elif word == "tab":
                    emit("\t")
        elif value not in (10, 13):
            emit(bytes([value]).decode("cp1252"))
    return "".join(characters), weights


def test_wordpad_generated_installed_end_to_end(tmp_path):
    if not _interactive_desktop():
        pytest.skip("WordPad integration needs a visible interactive Windows desktop")
    executable = Path(os.environ.get("AUTO_CLI_WORDPAD_EXE", "C:/Program Files/Windows NT/Accessories/wordpad.exe")).resolve()
    if not executable.is_file():
        pytest.skip(f"WordPad is not installed: {executable}")
    for module in ("uiautomation", "click", "prompt_toolkit", "setuptools", "wheel"):
        if importlib.util.find_spec(module) is None:
            pytest.fail(f"WordPad test enabled: install the existing test/GUI dependency {module}")
    rea = Path(os.environ.get("REA_BIN", ""))
    if not rea.is_file() or not shutil.which("node"):
        pytest.fail("WordPad test enabled: set REA_BIN to an installed rea.mjs and put Node.js on PATH")
    from auto_cli import gui
    try:
        existing = gui.find_window(process="WordPadClass", executable=str(executable))
    except gui.GuiError:
        pytest.skip("close existing WordPad windows before this isolated integration test")
    if existing is not None:
        pytest.skip("close existing WordPad windows before this isolated integration test")

    destination = Path(os.environ["AUTO_CLI_TEST_ARTIFACTS"]) if os.environ.get("AUTO_CLI_TEST_ARTIFACTS") else tmp_path
    root = destination.resolve() / ("wordpad-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    repository = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PIP_DISABLE_PIP_VERSION_CHECK="1")
    env.pop("PYTHONPATH", None)
    generation_env = dict(env, PYTHONPATH=str(repository))
    _run(root, "generation", [sys.executable, "-m", "auto_cli.cli", "auto", "--target", str(executable),
                             "--launch", "--name", "wordpad", "--out", str(root / "generated"),
                             "--wait", "25", "--timeout", "120"],
         cwd=outside, env=generation_env, timeout=900)
    harness = root / "generated/wordpad/agent-harness"
    assert (root / "generated/evidence-bundle.json").is_file()
    verification = json.loads((harness / "verification.json").read_text(encoding="utf-8"))
    assert any(check["kind"] == "gui_window" and check["state"] == "verified" for check in verification["checks"])
    assert (harness / "cli_anything/wordpad/utils/document_runner.py").is_file()

    environment = root / "installed-venv"
    _run(root, "venv", [sys.executable, "-m", "venv", str(environment)], cwd=outside, env=env)
    python = environment / "Scripts/python.exe"
    packages = environment / "Lib/site-packages"
    shared = [str(Path(path).resolve()) for path in site.getsitepackages() if Path(path).is_dir()]
    (packages / "installed-test-dependencies.pth").write_text("\n".join(shared) + "\n", encoding="utf-8")
    _run(root, "install", [str(python), "-m", "pip", "install", "--no-deps", "--no-build-isolation",
                          "--no-cache-dir", "--no-index", str(harness)], cwd=outside, env=env)
    console = environment / "Scripts/cli-anything-wordpad.exe"
    assert console.is_file()
    installed = _run(root, "installed-location", [str(python), "-c",
                     "import cli_anything.wordpad; print(cli_anything.wordpad.__file__)"], cwd=outside, env=env)
    assert Path(installed.stdout.strip()).resolve().is_relative_to(packages.resolve())

    def command(label, *args, expected=0):
        return _run(root, label, [str(console), "--json", *args], cwd=outside, env=env, expected=expected)

    def no_window(label):
        deadline = time.monotonic() + 15
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            result = command(f"{label}-{attempt}", "ui-info", expected=None)
            if result.returncode == 2 and _payload(result).get("error") == "missing_target":
                return
            time.sleep(0.2)
        pytest.fail(f"an unexpected WordPad window remains open; see {root}")

    _run(root, "help", [str(console), "--help"], cwd=outside, env=env)
    info = _payload(command("info", "info"))
    assert info["target"]["exists"] and info["runtime"]["python"]
    assert info["capabilities"]["gui"]["installed"]
    no_window("generation-closed")

    outputs, expected_texts = [], []
    for number in (1, 2):
        marker = f"CLIENT_NAME_{number}"
        source = root / f"input-{number}.rtf"
        original = ("{\\rtf1\\ansi Release checklist\\par Client: " + marker + "\\par Status: draft\\par}").encode("ascii")
        source.write_bytes(original)
        output = root / f"approved-{number}.rtf"
        name, paragraph = f"Alex Rowan {number}", f"Approved for review. {number}"
        arguments = ["workflow", "--input", str(source), "--output", str(output), "--old", marker,
                     "--new", name, "--text", paragraph, "--heading", "Release checklist"]
        denied = _payload(command(f"no-confirmation-{number}", *arguments, expected=2))
        assert not denied["ok"] and "--yes" in denied["detail"]
        assert source.read_bytes() == original and not output.exists()
        no_window(f"denial-no-window-{number}")

        result = _payload(command(f"workflow-{number}", *arguments, "--yes"))
        assert result["ok"] and all(result["verification"][key] for key in
                                     ("reopened_in_app", "text_matches", "heading_bold", "source_unchanged"))
        expected = f"Release checklist\nClient: {name}\nStatus: draft\n{paragraph}"
        assert result["text"] == expected
        assert source.read_bytes() == original
        assert output.read_bytes().startswith(b"{\\rtf")
        text, weights = _rtf_characters(output.read_bytes())
        for fragment, bold in (("Release checklist", True), ("Status: draft", False), (paragraph, False)):
            assert text.count(fragment) == 1, text
            start = text.index(fragment)
            assert all(weight == bold for weight in weights[start:start + len(fragment)]), (fragment, text)
        session = Path(result["session"])
        assert session.is_file() and session.with_name("events.jsonl").is_file()
        assert json.loads(session.with_name("workflow-report.json").read_text(encoding="utf-8"))["ok"]
        output_bytes = output.read_bytes()
        refused = _payload(command(f"existing-output-{number}", *arguments, "--yes", expected=2))
        assert not refused["ok"] and "overwrite" in refused["detail"]
        assert output.read_bytes() == output_bytes
        no_window(f"workflow-closed-{number}")
        outputs.append(output)
        expected_texts.append(expected)

    malformed = root / "malformed.rtf"
    malformed.write_bytes(b"{\\rtf1 unclosed")
    refused = _payload(command("malformed", "workflow", "--input", str(malformed), "--output", str(root / "bad-output.rtf"),
                               "--old", "marker", "--new", "name", "--text", "paragraph", "--heading", "heading", "--yes", expected=2))
    assert not refused["ok"] and "RTF" in refused["detail"]
    assert not (root / "bad-output.rtf").exists()
    no_window("malformed-no-window")

    # The generation process is gone. Public commands must discover this fresh
    # process without a captured PID/title or a direct call to a private runner.
    process = subprocess.Popen([str(executable), str(outputs[-1])])
    try:
        deadline = time.monotonic() + 20
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            result = command(f"retarget-info-{attempt}", "ui-info", expected=None)
            if result.returncode == 0 and _payload(result).get("pid") == process.pid:
                break
            time.sleep(0.2)
        else:
            pytest.fail(f"public ui-info could not find the fresh WordPad process; see {root}")
        tree = _payload(command("retarget-tree", "ui-tree"))
        assert any(node["name"] == "Rich Text Window" for node in tree["nodes"])
        read = _payload(command("retarget-read", "ui-read-text", "Rich Text Window"))
        assert read["text"].replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") == expected_texts[-1]
    finally:
        # This process only reopened our saved output; it has no pending edits.
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
