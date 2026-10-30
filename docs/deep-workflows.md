# Deep workflow generation

Auto-CLI can pass a generated harness and verified REA findings to Codex or
Claude Code. The agent is asked to add useful domain commands, composed
workflows, documentation, and regression tests. This is optional: the normal
generation pipeline works without a coding agent.

The available evidence determines what the agent can implement. A successful
run means the agent exited successfully and the resulting harness passed
Auto-CLI's checks. Review the commands and their application behavior before
depending on a new workflow.

## Choose the generation path

| Command | Use it when | Coding agent |
| --- | --- | --- |
| `complete` | An existing harness needs commands derived from readable database schemas or verified HTTP endpoints. | None; generation follows the supported schema/endpoint patterns. |
| `deep` | An existing, verified harness needs richer commands or multi-step workflows. | Select `--agent` in both modes; an installed/authenticated CLI is needed only for execution. |
| `auto` | You want recon, scaffolding, verification, completion, optional IPC/GUI capture, and validation in one run. | None by default; add `--agent codex` or `--agent claude` for a final handoff. |

Use `deep` when working on a harness you have already customized. `auto`
generates the scaffold again and can overwrite generated files; use a fresh
output directory when you need to retain an earlier harness.

## Prerequisites

Use Python 3.10 or newer. From a repository checkout, install the existing
development extras into the Python environment you will use for these commands:

```console
python -m pip install -e ".[dev]"
```

The extras provide pytest, Click, and prompt-toolkit for generated harness
execution and validation. Use the same `python` throughout this guide.

Fresh recon through `auto` also requires REA and a supported Node.js version;
follow the [main setup instructions](../README.md#getting-started-3-steps)
and check that REA is reachable:

```console
python -m auto_cli doctor
```

Standalone `deep` loads the saved evidence bundle and harness. It does not
locate or run REA. GUI handoffs also check the recorded executable against
the current file on disk, which must still be present and unchanged.

Generation includes an unmodified, pinned copy of CLI-Anything's original
`repl_skin.py` and its Apache 2.0 license. No separate download or checkout is
needed for validation. `--repl-skin PATH` is an optional existing-file override.
For Windows GUI capture, install the existing GUI extra as well:

```console
python -m pip install -e ".[dev,gui]"
```

For execution, install and authenticate your chosen `codex` or `claude` CLI
separately and make it available on PATH. Auto-CLI uses that existing account
and configuration. Preparation with `--prepare-only` needs neither agent
installation nor authentication and makes no coding-agent call.

## Prepare verified context first

`deep` requires an existing generated harness containing `setup.py`, a
generated backend, and `verification.json`. The verification report's target
must exactly match the target in the supplied evidence bundle. The report
must include either a verified/corrected HTTP or database check, or both a
verified live GUI window and successful text read from that same PID/window.
GUI evidence includes the executable path and SHA-256, live window identity,
and observation time. The handoff checks the current executable against that
identity; an updated binary requires fresh verification.

The gate checks the saved report rather than repeating live verification.
Refresh it when the application or available surface changes. Static IPC
findings remain `unverifiable`. `drive.json` and `gui.json` are accompanying
observations, not substitutes for `verification.json`; merely capturing a
tree does not prove reads, writes, formatting, or saving. Verified GUI reads
let an agent start from a supported live surface without requiring an HTTP
API or database.

The examples below assume `./my-app` is your target and has a readable
database or an HTTP endpoint that can be verified. Replace that path and the
name as needed. For an example database target, use the
[SQLite library quickstart](../README.md#copy-and-paste-quickstart-windows-powershell).
Start the application first if its endpoint must be live.

### PowerShell

Generate the baseline harness, then prepare a Codex handoff:

```powershell
python -m auto_cli auto --target ./my-app --name my-app --out forge-out
if ($LASTEXITCODE -ne 0) { throw 'Baseline generation failed; inspect its reports before continuing' }

python -m auto_cli deep `
    --bundle ./forge-out/evidence-bundle.json `
    --dir ./forge-out/my-app/agent-harness `
    --agent codex --task "Add search and export workflows using verified data" `
    --prepare-only
if ($LASTEXITCODE -ne 0) { throw 'Handoff preparation failed' }

Get-Content ./forge-out/my-app/agent-harness/DEEP-REPORT.md
```

The command prints the new run directory. Inspect its `prompt.md`,
`verification.json`, and `plan.json`. When ready, execute one of these
alternatives. Each execution creates a new run directory using the current
evidence and harness.

```powershell
# Codex
python -m auto_cli deep `
    --bundle ./forge-out/evidence-bundle.json `
    --dir ./forge-out/my-app/agent-harness `
    --agent codex --task "Add search and export workflows using verified data"
```

```powershell
# Claude Code
python -m auto_cli deep `
    --bundle ./forge-out/evidence-bundle.json `
    --dir ./forge-out/my-app/agent-harness `
    --agent claude --task "Add search and export workflows using verified data"
```

### Bash

Generate the baseline harness, then prepare a handoff. Here the selected
agent is Claude Code; either agent can be prepared without being installed.

```bash
python -m auto_cli auto --target ./my-app --name my-app --out forge-out
if [ "$?" -ne 0 ]; then echo 'Baseline generation failed; inspect its reports before continuing' >&2; exit 1; fi

python -m auto_cli deep \
    --bundle ./forge-out/evidence-bundle.json \
    --dir ./forge-out/my-app/agent-harness \
    --agent claude --task "Add search and export workflows using verified data" \
    --prepare-only
if [ "$?" -ne 0 ]; then echo 'Handoff preparation failed' >&2; exit 1; fi

cat ./forge-out/my-app/agent-harness/DEEP-REPORT.md
```

After reviewing the saved context, choose one execution command:

```bash
# Claude Code
python -m auto_cli deep \
    --bundle ./forge-out/evidence-bundle.json \
    --dir ./forge-out/my-app/agent-harness \
    --agent claude --task "Add search and export workflows using verified data"
```

```bash
# Codex
python -m auto_cli deep \
    --bundle ./forge-out/evidence-bundle.json \
    --dir ./forge-out/my-app/agent-harness \
    --agent codex --task "Add search and export workflows using verified data"
```

To refresh verification on an existing harness in either shell:

```console
python -m auto_cli verify --bundle ./forge-out/evidence-bundle.json --dir ./forge-out/my-app/agent-harness
```

Read `VERIFICATION.md` afterward. Completing `verify` alone is insufficient;
inspect whether its checks satisfy the handoff gate. For an already-running
Windows application, add its class and, if necessary, a current selector:

```powershell
python -m auto_cli verify --bundle ./forge-out/evidence-bundle.json --dir ./forge-out/my-app/agent-harness --gui-process WordPadClass --gui-pid 1234
```

Replace the example PID with the actual current process. The selected window
must belong to the bundle's executable target. This performs discovery and
text reads without editing the document.

## Include the handoff in `auto`

`auto --agent` runs the normal pipeline first. It attempts the handoff only
after base validation passes and any requested GUI leg succeeds. The same
verification gate then applies. The optional `--repair` pass applies to the
baseline generation, before the coding agent runs.

PowerShell preparation with Claude Code selected:

```powershell
python -m auto_cli auto `
    --target ./my-app --name my-app --out forge-out `
    --agent claude `
    --agent-task "Add search and export workflows using verified data" `
    --agent-timeout 1200 --agent-prepare-only
```

Bash preparation with Codex selected:

```bash
python -m auto_cli auto \
    --target ./my-app --name my-app --out forge-out \
    --agent codex \
    --agent-task "Add search and export workflows using verified data" \
    --agent-timeout 1200 --agent-prepare-only
```

Omit `--agent-prepare-only` for an end-to-end execution. To execute against
the harness you just inspected without regenerating it, use the standalone
`deep` command instead. `auto --agent-prepare-only` still performs recon,
generation, verification, and baseline validation; the flag skips only the
coding-agent execution and its subsequent validation.

For a GUI-only WordPad target, the direct launch can supply the verified
window/read evidence and keep the application alive through the handoff:

```powershell
$wordpad = Join-Path $env:ProgramFiles 'Windows NT\Accessories\wordpad.exe'
python -m auto_cli auto --target $wordpad --launch --name wordpad --out gui-out --agent codex --agent-task "Extend the verified document workflow" --agent-prepare-only
```

Bare `--launch` launches the executable target itself. Auto-CLI keeps its
owned launch running through GUI capture, verification, and the optional
agent stage, and cleans it up when the run ends. Omit `--agent-prepare-only`
to execute the installed agent. The recognized English WordPad adapter
already supplies a document workflow without an agent; see
[Windows workflows](windows-workflows.md) before commissioning extra code.

## Options

| Purpose | `deep` | `auto` |
| --- | --- | --- |
| Existing evidence | `--bundle FILE` (required) | Generated by recon; select `--target PATH` or `--url URL`. |
| Harness location | `--dir DIR` (required) | `--out OUT` creates `OUT/<name>/agent-harness`. |
| Agent | `--agent codex\|claude` (required) | `--agent codex\|claude` (optional). |
| Workflow priorities | `--task TEXT` | `--agent-task TEXT`. |
| Agent time limit | `--timeout SECONDS` | `--agent-timeout SECONDS`. |
| Prepare without executing | `--prepare-only` | `--agent-prepare-only`. |

The default agent time limit is 900 seconds and must be greater than zero.
It covers the agent process; post-agent validation runs afterward with its
own limits. In `auto`, `--timeout` controls REA probes, so use
`--agent-timeout` for the coding agent. Agent-specific `auto` options require
`--agent`. Without task text, the prompt asks the agent to choose useful
workflows supported by the verified evidence.

## Saved evidence, output, and history

Every accepted preparation or execution creates a new directory under the
harness. Runs do not overwrite earlier run directories:

```text
agent-harness/
  DEEP-REPORT.md                 latest reported run
  deep-workflow/
    run-<random>/
      evidence-bundle.json       saved recon evidence and provenance
      verification.json          saved runtime checks
      plan.json                  classifier output for this bundle
      prompt.md                  exact handoff prompt
      drive.json                 copied only if present in the harness
      gui.json                   copied only if present in the harness
      agent.stdout.log           created when agent launch is attempted
      agent.stderr.log           created when agent launch is attempted
      validation.json            written after a zero-exit agent is validated
      report.json                prepared/completed/failed/timeout result
```

If agent lookup fails before launch, logs are absent. Preparation writes no
agent logs or validation result. A nonzero agent exit or timeout skips
post-agent validation, so `validation.json` is absent. `report.json` records
the chosen agent, run directory, status, agent return code when available,
and error text. `DEEP-REPORT.md` points to the latest reported run.

The snapshots preserve the inputs; they are not backups of the harness
code. The agent edits the existing harness in place. Failed runs can leave
partial changes, and neither retries nor preparation roll them back. Keep
your own commit or copy before execution, review partial changes after a
failure, and use `deep` to retry against the intended harness state.
Preparing again also creates another run; it does not resume an earlier
agent session or execute a previously saved prompt.

## What execution and validation cover

The prompt tells the agent to use verified/corrected findings, preserve
failed or unverifiable findings as limitations, and retain JSON output,
confirmation gates, backups before writes, read-only defaults, and origin
restrictions. It asks for regression tests in the generated package's test
directory and updates to its skill guidance and README. The agent is told
to document an evidence gap when a requested workflow cannot be supported.

After a zero agent exit, Auto-CLI checks required files, Python compilation,
package imports, the JSON flag and command presence, registry metadata, and
the repl skin. It runs the entire generated
`cli_anything/<module>/tests/` directory with the same Python interpreter.
These tests are intended to use temporary databases or controlled responses
without requiring the application. The report explicitly labels the result
as package/unit validation with `live_workflow_verified: false`. Passing
validation does not prove that a new workflow works against a live target.
The agent is asked to run and record applicable real-application checks
separately; inspect those results and the changed code before depending on
the workflow.

Auto-CLI invokes Codex with `exec --sandbox workspace-write` and Claude Code
with `--print --permission-mode acceptEdits`. It does not request permission
bypass flags. The chosen agent's normal configuration and permissions still
apply, and shell commands used to inspect or test the harness may require
permission in that configuration. The prompt's requested edit scope is the
generated harness; it is not a separate containment mechanism.

Execution lets the chosen CLI read the handoff evidence and harness, using
its configured provider and existing account/billing. Auto-CLI adds no
required runtime dependency for the handoff. The automated repository tests
exercise handoff preparation, execution handling, and failure cases without
calling paid agents; see [CI coverage](ci.md) for the separate real-app tests.

## Results and troubleshooting

| Result | Exit status | Meaning and next step |
| --- | --- | --- |
| `prepared` | `0` | Context was saved. No agent ran and no new implementation was validated. |
| `completed` | `0` | The agent exited zero and post-agent validation passed. Review its edits and test the target workflow. |
| `failed` | `2` | Agent lookup, launch, execution, or post-agent validation failed. Inspect `report.json`, available agent logs, and `validation.json` when present. |
| `timeout` | `2` | The agent exceeded its time limit. Auto-CLI attempts to stop it and its descendants; inspect logs and partial changes before retrying. |
| Input error | `2` for `deep` | The harness, bundle, verification, or options are unsuitable. Errors before preparation do not create a run directory. |

Earlier `auto` stages retain their own errors, including exit `1` for a
missing REA installation or an invalid/missing target. A failed handoff makes
the overall `auto` result fail with exit `2`.

For a missing or mismatched verification report, rerun `verify` using the
same bundle and harness. If all checks are failed or unverifiable, make the
target accessible and verify it again. For a GUI-only app, use `verify` with
`--gui-process` and the current PID/title as needed, or `auto --launch` for a
direct executable target. A saved tree or static IPC observation alone cannot
unlock `deep`. For missing agents or authentication/permission failures,
check PATH and the agent's own configuration, then inspect its stderr log
if one was created.

For validation failures, review the saved check details and rerun:

```console
python -m auto_cli validate --dir ./forge-out/my-app/agent-harness
```

`--repair` does not repair coding-agent changes after a failed handoff.
Adjust the code or task and retry `deep` after reviewing the current files.
Ctrl+C also triggers agent-process cleanup, but the interrupt propagates;
the latest report may not be refreshed, so inspect that run's available
context and logs.
