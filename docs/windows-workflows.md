# Windows GUI and document workflows

Auto-CLI can discover a live Windows application's UI Automation controls,
generate a CLI that resolves its current window, and hand verified GUI
findings to an optional coding agent. For recognized English WordPad, it
also installs document commands that edit and save an RTF through the app.

The installed generated CLI has completed this open/edit/Save As/reopen
round trip against native English WordPad on a local Windows desktop.
That result covers this adapter and target; it is separate from hosted CI
and is not a claim of automatic support for other editors.

## Generate a WordPad CLI

Use an interactive Windows desktop with the native English WordPad installed.
The adapter expects `WordPadClass`, a native RichEdit document control, and
English Save As controls. It does not download WordPad or adapt arbitrary
editors automatically. A missing executable or an incompatible control
surface produces an explicit error.

From an activated development environment in this checkout:

```powershell
python -m pip install -e ".[dev,gui]" "setuptools>=68" wheel
python -m auto_cli doctor
$wordpad = Join-Path $env:ProgramFiles 'Windows NT\Accessories\wordpad.exe'
if (-not (Test-Path -LiteralPath $wordpad -PathType Leaf)) { throw 'Native WordPad is not installed at this path' }
python -m auto_cli auto --target $wordpad --launch --name wordpad --out out
if ($LASTEXITCODE -ne 0) { throw 'Generation failed; inspect the reports in out' }
python -m pip install -e ./out/wordpad/agent-harness
```

REA and its supported Node.js runtime must already be available; see the
[main setup](../README.md#getting-started-3-steps). The built-in Windows GUI
driver does not require a native binary-analysis provider. Unavailable REA
probes remain recorded limitations.

Bare `--launch` starts the executable target directly. WordPad needs no
manually captured PID or class flag. Its owned launch stays alive through
GUI verification, validation, and any optional agent handoff, then Auto-CLI
cleans up that launch. The generated package declares its Windows
`uiautomation` dependency and includes the original CLI-Anything REPL skin
and Apache 2.0 license. No external REPL-skin download is required.

## Run a complete document workflow

Create this small fixture in a fresh working directory, or supply your own
existing RTF with unique marker and heading text:

```powershell
Set-Content -LiteralPath input.rtf -Encoding Ascii -Value '{\rtf1\ansi\deff0{\fonttbl{\f0 Calibri;}}\f0\fs24 Release checklist\par Client: CLIENT_NAME\par Status: draft\par}'
cli-anything-wordpad --json workflow --input input.rtf --output approved.rtf --old CLIENT_NAME --new "Alex Rowan" --text "Approved for review." --heading "Release checklist" --yes
```

The workflow opens a private copy of the entire original RTF, replaces the
unique marker, appends a paragraph, and makes the unique heading bold.
Edits use the native RichEdit control and UIA selection/formatting; the
implementation does not generate the final RTF by rewriting it offline.
WordPad saves it through its actual Save As dialog. The workflow reopens the
saved file in WordPad, checks text and heading weight, and checks the source
file's SHA-256 stayed unchanged.

The output must be a new `.rtf` path in an existing directory. Overwrites,
malformed inputs, missing or repeated selections, and unsupported controls
are refused. `--yes` is required. Each successful workflow reports its
verification separately from package/unit test results.

Evidence stays beside the output:

```text
.auto-cli-workflows/
  run-<random>/
    session.json          owned process/window identity and source hash
    events.jsonl          recorded workflow actions and verification
    workflow-report.json  successful final result and saved-output hash
    wordpad-<random>/
      working.rtf         private full-RTF copy
```

A failed run preserves the working copy and editor for inspection; a final
report may be absent if verification was not reached. Inspect the returned
error and retained session instead of treating file existence as success.
Repeat with a new output path to run the workflow again.

## Use individual document commands

Editing commands operate only on a session created by `open`, whose recorded
PID and window handle must still match the owned WordPad window. They do not
silently attach to an unrelated document after the app closes or restarts.

```powershell
cli-anything-wordpad --json open input.rtf --session edit.json --yes
cli-anything-wordpad --json read --session edit.json
cli-anything-wordpad --json replace CLIENT_NAME "Alex Rowan" --session edit.json --yes
cli-anything-wordpad --json append "Approved for review." --session edit.json --yes
cli-anything-wordpad --json bold "Release checklist" --session edit.json --yes
cli-anything-wordpad --json save-as approved-manual.rtf --session edit.json --yes
```

Choose a new session file for each `open`. Individual commands expose each
step; `workflow` additionally performs the composed reopen verification and
closes its successfully saved windows. `read` can also inspect an existing
live WordPad window with an explicit current `--pid` or `--title`, without
claiming ownership for later edits.

## Inspect and target any captured GUI surface

Generated `info` returns target/runtime/capability metadata when no database
was discovered; it retains the database overview for database targets.
`ui-info` checks the current live window. JSON-mode failures are structured
and exit nonzero.

```powershell
cli-anything-wordpad --json info
cli-anything-wordpad --json ui-info
cli-anything-wordpad --json ui-tree --max-depth 12 --max-nodes 400
cli-anything-wordpad --json ui-read-text "Rich Text Window"
cli-anything-wordpad --json ui-read-text "Rich Text Window" --pid 1234 --title "input"
```

Replace `1234` with a current PID. All `ui-*` commands accept `--pid` and
`--title`. Defaults use the captured executable and window class; the old
PID and document title are evidence only. Restarting the application or
changing its document title does not require regeneration. Multiple matching
windows fail explicitly until you narrow the target; all supplied selectors
must match. If capture could not resolve the executable, an explicit current
selector is required. An executable path that cannot be verified never
silently falls back to another process.

`ui-tree` and `ui-menus` use depth/node limits of 12/400 by default and accept
bounded overrides. Text patterns are included alongside Value, Invoke,
ExpandCollapse, SelectionItem and Toggle. Control paths can be unique
suffixes, so prefer `Rich Text Window` over a path containing a mutable title.

Generic actions `ui-invoke`, `ui-select`, and `ui-set-text` require `--yes`.
Save/close/delete-like paths also need `--allow-destructive`. These commands
are building blocks; discovery alone does not implement arbitrary
application-specific editing or saving.

## Verified GUI evidence and coding agents

`auto` verifies the selected window belongs to the target executable and
attempts a live text read. `verification.json` records executable path and
SHA-256, window identity, time, and read success; it omits document text from
that verification record. The GUI capture can still contain control names
and other observed UI details.

This verified window/read pair can unlock `deep` without a database or API.
A static `gui.json` alone cannot. An unchanged executable on disk is required
when preparing the handoff; verification of a read does not certify writes,
formatting, or Save As. See [deep workflow generation](deep-workflows.md) for
agent prerequisites, logs, permission behavior, and validation scope.

The normal generation/validation result proves package checks and generated
tests. A successful `workflow` result supplies the separate real-app
round-trip evidence. The [opt-in WordPad integration](ci.md#run-the-live-wordpad-integration)
requires an interactive Windows desktop; hosted CI does not run it.
