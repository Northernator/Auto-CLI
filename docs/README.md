# Auto-CLI documentation

Start with the [repository README](../README.md) for installation, the
PowerShell library quickstart, the command reference, and application targets.

| Task | Guide |
| --- | --- |
| Generate richer commands and multi-step workflows with Codex or Claude Code | [Deep workflow generation](deep-workflows.md) |
| Generate a native WordPad CLI, edit an RTF, Save As, reopen and verify it | [Windows document workflows](windows-workflows.md) |
| Inspect GitHub Actions results, understand coverage, or run the real-app tests locally | [CI and real-application tests](ci.md) |
| Generate a CLI for a small SQLite library and export its books | [Copy-and-paste quickstart](../README.md#copy-and-paste-quickstart-windows-powershell) |
| Drive a real Windows application through a generated CLI | [Notepad example](../examples/notepad-demo.ps1) |
| Record a terminal walkthrough of the library example | [Demo script](demo-script.md) |

The normal `auto` flow verifies discovered endpoints and databases, and can
verify a live Windows GUI against its target executable. It adds supported
domain commands without a coding agent, including the recognized native
WordPad document adapter. Generated packages include the original REPL skin
and license; no external skin download is needed. The optional `deep` step
hands verified evidence and the generated harness to an installed agent for
further implementation and tests. It also runs through `auto --agent`.

Use the [CI guide](ci.md) to distinguish automated test coverage from live GUI
demonstrations and external agent runs. A passing generated-harness test suite
does not prove every workflow works against every version of the target app.
