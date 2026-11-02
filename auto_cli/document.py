"""Install the explicit WordPad document adapter into a captured GUI harness."""

from pathlib import Path


DOCUMENT_CLI = '''

# ---- WordPad document workflows added by auto-cli --------------------

def _document_emit(ctx, command, **values):
    import subprocess
    import sys
    if command != "read" and not values.get("yes"):
        _emit(ctx, {"ok": False, "detail": "document changes require --yes"})
        ctx.exit(2)
    module = __package__ + ".utils.document_runner"
    argv = [sys.executable, "-m", module, command]
    for key, value in values.items():
        if value is True:
            argv.append("--" + key.replace("_", "-"))
        elif value is not None and value is not False:
            argv.append("--" + key.replace("_", "-") + "=" + str(value))
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=180)
        payload = json.loads(proc.stdout)
        if not isinstance(payload, dict):
            payload = {"ok": False, "detail": "document runner returned an invalid response"}
        elif proc.returncode and payload.get("ok"):
            payload = {"ok": False, "detail": "document runner exited " + str(proc.returncode)}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        payload = {"ok": False, "detail": str(exc)}
    _emit(ctx, payload)
    if not payload.get("ok"):
        ctx.exit(2)


@main.command("open")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.option("--session", required=True, type=click.Path(dir_okay=False))
@click.option("--yes", is_flag=True)
@click.pass_context
def document_open(ctx, input, session, yes):
    """Open a private RTF copy and return its owned editing session."""
    _document_emit(ctx, "open", input=input, session=session, yes=yes)


@main.command("read")
@click.option("--session", type=click.Path(exists=True, dir_okay=False))
@click.option("--pid", type=int)
@click.option("--title")
@click.pass_context
def document_read(ctx, session, pid, title):
    """Read the live WordPad document; no mutation."""
    _document_emit(ctx, "read", session=session, pid=pid, title=title)


@main.command("replace")
@click.argument("old")
@click.argument("new")
@click.option("--session", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--yes", is_flag=True)
@click.pass_context
def document_replace(ctx, old, new, session, yes):
    """Replace one unique text marker in an owned editing session."""
    _document_emit(ctx, "replace", old=old, new=new, session=session, yes=yes)


@main.command("append")
@click.argument("text")
@click.option("--session", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--yes", is_flag=True)
@click.pass_context
def document_append(ctx, text, session, yes):
    """Append a paragraph in an owned editing session."""
    _document_emit(ctx, "append", text=text, session=session, yes=yes)


@main.command("bold")
@click.argument("heading")
@click.option("--session", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--yes", is_flag=True)
@click.pass_context
def document_bold(ctx, heading, session, yes):
    """Make a unique heading bold, preserving surrounding formatting."""
    _document_emit(ctx, "bold", heading=heading, session=session, yes=yes)


@main.command("save-as")
@click.argument("output", type=click.Path(dir_okay=False))
@click.option("--session", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--yes", is_flag=True)
@click.pass_context
def document_save_as(ctx, output, session, yes):
    """Save through WordPad to a new RTF path; never overwrite."""
    _document_emit(ctx, "save-as", output=output, session=session, yes=yes)


@main.command("workflow")
@click.option("--input", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--output", required=True, type=click.Path(dir_okay=False))
@click.option("--old", required=True)
@click.option("--new", required=True)
@click.option("--text", required=True, help="paragraph to append")
@click.option("--heading", required=True, help="unique heading to make bold")
@click.option("--yes", is_flag=True)
@click.pass_context
def document_workflow(ctx, input, output, old, new, text, heading, yes):
    """Open, replace, append, bold, Save As, reopen and verify an RTF."""
    _document_emit(ctx, "workflow", input=input, output=output, old=old, new=new,
                   text=text, heading=heading, yes=yes)
'''


DOCUMENT_TESTS = '''"""Document adapter guards; no application or UIA dependency required."""
import json
import pytest
from cli_anything.MODULE.utils import document_runner as document


def test_document_consent_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(document.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(document.DocumentError, match="--yes"):
        document.workflow(tmp_path / "missing.rtf", tmp_path / "out.rtf", "old", "new", "text", "heading")


def test_document_original_and_existing_output_protected(tmp_path):
    source = tmp_path / "source.rtf"
    source.write_bytes(b"{\\\\rtf1 original}")
    with pytest.raises(document.DocumentError, match="already exists"):
        document._output(source, source)
    assert source.read_bytes() == b"{\\\\rtf1 original}"


def test_document_rejects_incomplete_rtf(tmp_path):
    source = tmp_path / "broken.rtf"
    source.write_bytes(b"{\\\\rtf1 incomplete")
    with pytest.raises(document.DocumentError, match="incomplete"):
        document._input(source)


def test_document_requires_rtf_output(tmp_path):
    with pytest.raises(document.DocumentError, match=".rtf"):
        document._output(tmp_path / "wrong.txt")


def test_document_preserves_trailing_paragraphs():
    assert document._normal("paragraph\\r\\r") == "paragraph\\n"


def test_document_rejects_foreign_session_before_ui_lookup(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"executable": "different.exe", "pid": 123, "window_handle": 1}))
    monkeypatch.setattr(document, "_window", lambda **kw: pytest.fail("must not look up foreign session"))
    with pytest.raises(document.DocumentError, match="different executable"):
        document._session(path)


def test_document_rejects_invalid_owned_identity(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"executable": document.EXECUTABLE, "pid": None, "window_handle": 1}))
    monkeypatch.setattr(document, "_window", lambda **kw: pytest.fail("must not select arbitrary window"))
    with pytest.raises(document.DocumentError, match="identity"):
        document._session(path)


def test_document_errors_are_json(capsys):
    assert document.main(["workflow"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
'''


def upgrade_scaffold(root: Path, report, executable: str = "") -> bool:
    """Only install for the explicitly recognized native WordPad application."""
    from .validate import _find_harness_root
    root = _find_harness_root(Path(root))
    target = executable or getattr(report, "executable", "")
    if report.process.lower() != "wordpadclass" or Path(target).name.lower() != "wordpad.exe":
        return False
    packages = [p for p in Path(root).glob("cli_anything/*") if p.is_dir() and p.name != "__pycache__"]
    if len(packages) != 1:
        return False
    pkg = packages[0]
    cli = pkg / f"{pkg.name}_cli.py"
    src = cli.read_text(encoding="utf-8")
    if "def document_workflow(" in src:
        return False
    anchor = '\n\nif __name__ == "__main__":'
    if anchor not in src or not (pkg / "utils" / "uia_runner.py").is_file():
        return False
    runtime = Path(__file__).with_name("document_runtime.py").read_text(encoding="utf-8")
    runtime = runtime.replace('EXECUTABLE = ""', f"EXECUTABLE = {str(Path(target).resolve())!r}", 1)
    (pkg / "utils" / "document_runner.py").write_text(runtime, encoding="utf-8")
    (pkg / "tests" / "test_document.py").write_text(DOCUMENT_TESTS.replace("MODULE", pkg.name), encoding="utf-8")
    cli.write_text(src.replace(anchor, DOCUMENT_CLI + anchor), encoding="utf-8")
    entry = "cli-anything-" + pkg.name.replace("_", "-")
    docs = f'''
## WordPad document workflow

This Windows adapter targets English WordPad with a native RichEdit document.
It adds `open`, `read`, `replace`, `append`, `bold`, `save-as` and `workflow`.
The workflow edits a private full-RTF copy, saves through WordPad's Save As
dialog, reopens the output and verifies the text and bold heading. The original
input stays unchanged. Output paths must be new; overwrites are refused.

```powershell
{entry} --json workflow --input input.rtf --output approved.rtf --old CLIENT_NAME --new "Alex Rowan" --text "Approved for review." --heading "Release checklist" --yes
```

Each run writes its session, private copy, event log and final verification
report under `.auto-cli-workflows` beside the output. Individual editing
commands require an owned session created by `open INPUT --session FILE --yes`.
Markers and headings must occur exactly once. Missing/ambiguous selections
fail explicitly. A failed run preserves its working copy and editor for inspection.
Do not confuse installing this adapter or passing fixture unit tests with a
successful real-app run; only a successful workflow response proves its app round trip.
'''
    for doc in (pkg / "README.md", pkg / "skills" / "SKILL.md", Path(root) / "README.md"):
        if doc.is_file():
            doc.write_text(doc.read_text(encoding="utf-8") + docs, encoding="utf-8")
    return True
