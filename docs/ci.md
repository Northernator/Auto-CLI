# CI and real-application tests

[CI runs](https://github.com/Northernator/Auto-CLI/actions/workflows/ci.yml)
are produced by `.github/workflows/ci.yml` on pushes, pull requests, and manual
dispatch. Adding the workflow does not itself constitute a successful hosted run.

The unit suite runs on GitHub-hosted Linux, Windows, and macOS with Python 3.10
and 3.13. Each runner uses Node.js 24.22.1 for the inspector transport tests and
installs setuptools and wheel so the generated-wheel regression runs. A separate
job builds the source distribution and wheel, installs the
wheel into a fresh environment, and runs `auto-cli --help` outside the checkout.
JUnit results and distributions are retained as run artifacts for 14 days.

The Linux integration job installs REA 6.8.0 with Node.js 24.22.1, plus
the test-only application versions in `.github/requirements-integration.txt`:

- **sqlite-utils 4.2.1:** create a real database using the application's CLI;
  inspect it with real REA; generate and verify a harness; execute generated
  info, get, and search commands; check the write guard; insert a row with
  confirmation; and use sqlite-utils to verify both the changed database and
  the pre-write backup.
- **Datasette 0.65.5:** run the real application as a subprocess on loopback;
  use REA to discover its absolute API URL from a small JavaScript client;
  verify the endpoint live; and run the generated domain command against the
  server, checking its returned columns and rows.

No REA outputs, HTTP responses, or subprocesses are mocked in these tests. The
Datasette client is a test fixture: this checks endpoint discovery and integration
with a real server, rather than reverse engineering Datasette's implementation or
browser UI. Desktop GUI automation, arbitrary third-party applications, and the
optional external coding-agent handoff are outside this automated coverage.

The integration job retains original REA envelopes, evidence bundles, generated
harnesses, verification reports, application logs, tool versions, and JUnit
results as the `real-app-evidence` artifact, including on test failure. Dependencies
are acquired during setup; test traffic stays on loopback. REA is installed in the
runner's temporary directory and is not an auto-cli package dependency. Top-level
application/tool versions and Actions revisions are pinned; transitive Python and
npm dependencies are not locked.

## Run locally

The normal suite needs no external applications:

```console
python -m pip install -e ".[dev]"
python -m pytest -q
```

For the integration suite, install Node.js 24.22.1, the test dependencies, and REA
outside the checkout. On PowerShell:

```powershell
python -m pip install -e ".[dev]" -r .github/requirements-integration.txt
$reaTools = Join-Path $env:TEMP 'auto-cli-rea'
npm install --prefix $reaTools --omit=optional --no-audit --no-fund rea-agents@6.8.0
$env:REA_BIN = Join-Path $reaTools 'node_modules/rea-agents/scripts/rea.mjs'
$env:AUTO_CLI_REAL_APPS = '1'
python -m pytest -q tests/test_integration_apps.py
```

On Bash:

```bash
python -m pip install -e '.[dev]' -r .github/requirements-integration.txt
rea_tools="$(mktemp -d)"
npm install --prefix "$rea_tools" --omit=optional --no-audit --no-fund rea-agents@6.8.0
export REA_BIN="$rea_tools/node_modules/rea-agents/scripts/rea.mjs"
AUTO_CLI_REAL_APPS=1 python -m pytest -q tests/test_integration_apps.py
```

Linux needs `ps` from `procps` for REA's process ownership checks; GitHub-hosted
Ubuntu supplies it. Set `AUTO_CLI_TEST_ARTIFACTS` to a fresh directory to preserve
integration artifacts locally. Without `AUTO_CLI_REAL_APPS=1`, these two tests are
explicitly skipped; when enabled, missing applications, Node.js, or `REA_BIN`
cause a failure rather than a silent skip. A failed REA capability or app startup
also fails the integration job.

The workflow uses the official
[GitHub Python build/test pattern](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
and REA's documented
[noninteractive CI installation approach](https://github.com/morluto/rea/blob/main/docs/ci-analysis.md).
