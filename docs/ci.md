# CI and real-application tests

[![CI](https://github.com/Northernator/Auto-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Northernator/Auto-CLI/actions/workflows/ci.yml)

Open [CI runs](https://github.com/Northernator/Auto-CLI/actions/workflows/ci.yml),
select the branch or pull request you are reviewing, and open the run for its
commit SHA. Check each job's conclusion and logs. The badge is a quick status
indicator; a successful run for another commit does not establish this commit's
result. The [workflow](../.github/workflows/ci.yml) runs on pushes, pull requests,
and manual dispatch. GitHub's [run-log guide](https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs)
explains how to inspect failed steps.

## What the eight jobs prove

| Jobs | Environment | Checks |
| --- | --- | --- |
| Six `Tests` jobs | Linux, Windows, and macOS, each with Python 3.10 and 3.13; Node.js 24.22.1 | Run the ordinary pytest suite, including generated-harness tests, a real Node inspector transport, and a generated-wheel contents check. |
| `Build and install package` | Linux, Python 3.13 | Build the source distribution and wheel, install the wheel into a fresh environment, and run `auto-cli --help` outside the checkout. |
| `Real REA, sqlite-utils and Datasette` | Linux, Python 3.13; Node.js 24.22.1 | Run the two opt-in application integrations described below with actual REA and app subprocesses. |

Each matrix job runs independently, so one platform failure does not cancel the
other combinations. The integration job has no `continue-on-error` or automatic
capability skips. Repository branch-protection requirements are configured
separately from this workflow.

For a dated local reference, the Windows validation on **30 October 2026** with
Python 3.11.9 and Node.js 24.22.1 reported **164 passed, 1 skipped** with the two
real-app tests enabled using REA 6.8.0. The ordinary invocation with integrations
disabled reported **162 passed, 3 skipped**: the two opt-in integrations and one
generated-runner entry-point case. These are historical local results; use the
run for the commit under review for current hosted evidence. Counts can change
as tests are added. Installing `wheel` ensures the generated-wheel test runs
instead of contributing an additional skip.

## Real-app coverage and its limits

The integrations in [test_integration_apps.py](../tests/test_integration_apps.py)
exercise these application workflows:

- **sqlite-utils 4.2.1:** create a database using the application's CLI; inspect
  it with real REA; generate and verify a harness; execute generated info, get,
  and search commands; check the write guard; insert a row with confirmation;
  and use sqlite-utils to verify both the changed database and the pre-write
  backup.
- **Datasette 0.65.5:** start the real server on loopback; use REA to discover
  its absolute API URL from a small JavaScript client; verify that endpoint
  live; and execute the generated domain command, checking returned columns
  and rows.

The application tests do not mock REA outputs, HTTP responses, or subprocesses.
The Datasette client is a fixture: this covers endpoint discovery and a working
server connection, rather than reverse engineering Datasette's implementation
or browser UI. Desktop GUI automation and arbitrary third-party applications
remain outside this automated coverage.

The database test also covers a bug found during real Windows testing: REA can
report `library.db` before the absolute local-filesystem finding. For a direct
database-file target, verification must resolve that relative path beside the
file. A focused, platform-independent regression lives in
[test_verify.py](../tests/test_verify.py).

The optional [deep coding-agent workflow](deep-workflows.md) has a separate
coverage boundary. [test_deep.py](../tests/test_deep.py) checks handoff preparation,
agent invocation, failure handling, and reports using mocked agent contracts or
local test processes. [test_validate_deep.py](../tests/test_validate_deep.py)
checks that newly added workflow tests are collected. CI does not launch an
authenticated Codex or Claude Code session, evaluate an agent's generated
implementation, or require paid provider credentials. Live external-agent runs
need the installed, authenticated agent and may use its account allowance.

## Download the evidence

Open the chosen Actions run's summary and its **Artifacts** section. Download
the relevant bundle while signed in to GitHub with repository read access;
GitHub documents the process in its
[artifact guide](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).
This workflow retains uploaded artifacts for **14 days**.

| Artifact | Contents and use |
| --- | --- |
| `tests-<os>-py<version>` | A matrix job's JUnit XML, written as `test-results/unit.xml`; inspect failures, skipped cases, and test durations. |
| `distributions` | The source archive and wheel from `dist/`, after a successful package build and installation smoke test. |
| `real-app-evidence` | JUnit results from `test-results/real-apps.xml`, plus per-test REA envelopes, `<command>.probe.json` (including exit status), `<command>.stderr.log`, `evidence-bundle.json`, generated harnesses, verification JSON/Markdown, `datasette.log`, and `tool-versions.json`. |

Test-result and real-app uploads run even after a test failure and preserve the
files produced before it. An installation or startup failure may leave only
partial evidence; read the failed step's log when an expected file is absent.
The tool-version JSON records Python, Node.js, REA, Datasette, and sqlite-utils.

## Reproduce the ordinary suite

Use a checkout of the same commit, Python 3.10+ with pip and venv, and Node.js
24.22.1 on `PATH`. Match Python 3.10 or 3.13 when reproducing a matrix failure.
Git is needed to obtain the checkout. An ordinary run does not require REA,
Datasette, sqlite-utils, a browser, a native reverse-engineering provider, or
coding-agent authentication; it does require Node.js for the inspector tests.

Create and activate a virtual environment from the repository root:

```powershell
# PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
# Bash
python -m venv .venv
source .venv/bin/activate
```

Then run the same installation and test commands used by the matrix:

```console
node --version
python -m pip install -e ".[dev]" "setuptools>=68" wheel
python -m pytest -q -rs --junitxml=test-results/unit.xml
```

For that ordinary invocation, leave `AUTO_CLI_REAL_APPS` unset. Setuptools and
wheel support the generated-package regression; these are development tools,
not added auto-cli runtime dependencies. Reproduce package-building failures
with:

```console
python -m pip install build==1.4.0
python -m build
```

The workflow's **Smoke test the wheel outside the checkout** step contains the
exact Linux commands for its isolated installation check.

## Run the live application tests

Start from the activated environment above. Install the test-only application
pins from [requirements-integration.txt](../.github/requirements-integration.txt).
The examples install REA into a unique temporary directory outside the checkout,
keep the evidence there, and restore the calling shell's environment afterward.
Linux also needs `ps` from `procps` for REA's process ownership checks;
GitHub-hosted Ubuntu supplies it.

On PowerShell:

```powershell
python -m pip install -r .github/requirements-integration.txt
$integrationRoot = Join-Path $env:TEMP ('auto-cli-integration-' + [guid]::NewGuid().ToString('N'))
$reaTools = Join-Path $integrationRoot 'tools'
$testArtifacts = Join-Path $integrationRoot 'evidence'
npm install --prefix $reaTools --omit=optional --no-audit --no-fund rea-agents@6.8.0
if ($LASTEXITCODE -ne 0) { throw 'REA installation failed' }
$previousReaBin = $env:REA_BIN
$previousRealApps = $env:AUTO_CLI_REAL_APPS
$previousArtifacts = $env:AUTO_CLI_TEST_ARTIFACTS
try {
    $env:REA_BIN = Join-Path $reaTools 'node_modules/rea-agents/scripts/rea.mjs'
    $env:AUTO_CLI_REAL_APPS = '1'
    $env:AUTO_CLI_TEST_ARTIFACTS = $testArtifacts
    python -m pytest -q -rs tests/test_integration_apps.py --junitxml="$testArtifacts/real-apps.xml"
    if ($LASTEXITCODE -ne 0) { throw 'Real-app tests failed; inspect retained evidence' }
} finally {
    $env:REA_BIN = $previousReaBin
    $env:AUTO_CLI_REAL_APPS = $previousRealApps
    $env:AUTO_CLI_TEST_ARTIFACTS = $previousArtifacts
    Write-Output "Evidence directory: $testArtifacts"
}
```

On Bash, a subshell confines the temporary environment overrides:

```bash
python -m pip install -r .github/requirements-integration.txt
(
    set -e
    integration_root="$(mktemp -d)"
    echo "Evidence directory: $integration_root/evidence"
    npm install --prefix "$integration_root/tools" --omit=optional --no-audit --no-fund rea-agents@6.8.0
    export REA_BIN="$integration_root/tools/node_modules/rea-agents/scripts/rea.mjs"
    export AUTO_CLI_REAL_APPS=1
    export AUTO_CLI_TEST_ARTIFACTS="$integration_root/evidence"
    export COLUMNS=4096
    python -m pytest -q -rs tests/test_integration_apps.py --junitxml="$AUTO_CLI_TEST_ARTIFACTS/real-apps.xml"
)
```

The tests stop the Datasette subprocess when finished. The examples leave the
temporary tools and evidence intact for review; delete that specific temporary
directory manually when no longer needed. They do not change your Node.js
installation. Check `node --version` before running to confirm the requested
runtime is active.

Without `AUTO_CLI_REAL_APPS=1`, the two tests are explicitly skipped. When
enabled, missing applications, Node.js, or `REA_BIN` cause a failure instead of a
silent skip. An unsupported REA capability, unsuccessful discovery, or failed
application startup also fails the test. Use a fresh evidence directory on each
run to avoid reusing an earlier database or client fixture.

The Linux integration job and Bash example set `COLUMNS=4096` because REA 6.8.0
uses `ps` command output to confirm its worker's executable identity. Procps can
otherwise truncate a long Node.js executable path at its default display width,
causing cleanup to fail even after analysis produces a complete evidence
envelope. A wider display preserves the identity check; it does not disable
cleanup verification or accept a nonzero REA exit status. The Bash override is
confined to the subshell.

## Investigate a failure and rerun

First inspect the failed job and step for the relevant commit. For a dependency
installation failure, check package acquisition and runtime versions before
debugging test logic. For a test failure, download its JUnit and available
real-app evidence, then reproduce with the same Python minor and Node version.
If REA returns an evidence envelope but exits unsuccessfully, inspect its
`.probe.json` and `.stderr.log` as well as the envelope. In particular, an
`owned launcher command identity did not match` error with a visibly shortened
Node path calls for the Linux display-width setting above.

These focused commands run from the configured environment:

```console
python -m pytest -q tests/test_drive.py
python -m pytest -q tests/test_verify.py::test_verify_relative_rea_database_finding_for_file_target
python -m pytest -q tests/test_deep.py tests/test_validate_deep.py
```

To isolate one live app, replace `tests/test_integration_apps.py` in either
opt-in example with one of these node IDs, keeping its environment setup:

```text
tests/test_integration_apps.py::test_sqlite_utils_recon_to_generated_workflow
tests/test_integration_apps.py::test_datasette_discovered_endpoint_to_generated_command
```

Maintainers with repository write access can use **Re-run jobs → Re-run failed
jobs** on the run summary. A rerun uses the original commit and ref; push a new
commit to test a fix. See GitHub's [rerun guide](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs).

## Pins and dependency scope

CI uses REA **6.8.0**, Node.js **24.22.1**, Datasette **0.65.5**, sqlite-utils
**4.2.1**, and the package build frontend **1.4.0**. The four GitHub Actions are
pinned to commit revisions in the workflow. Python minor versions are selected
explicitly, while their patch versions and hosted runner images can change.
Top-level application/tool versions are pinned; transitive Python/npm
dependencies, pytest, setuptools, and wheel are not locked.

REA and the two real apps are test tools outside the auto-cli runtime dependency
set. Setup downloads dependencies; application test traffic stays on loopback.
The workflow requests `contents: read` and does not require provider API keys or
user-supplied secrets. Its setup follows the official
[GitHub Python build/test pattern](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
and REA's [noninteractive CI installation approach](https://github.com/morluto/rea/blob/main/docs/ci-analysis.md).
