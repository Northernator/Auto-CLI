# auto-cli

**Point it at software. Get an agent-native CLI.**

![auto-cli pipeline: from app to harness](docs/pipeline.jpg)

[REA](https://github.com/morluto/rea) (`npm i -g rea-agents`) reverse-engineers
software for agents. [CLI-Anything](https://github.com/HKUDS/CLI-Anything) is a
registry of hand-written CLI harnesses that make software agent-native. The
missing piece between them: REA tells you *what an app's automation surface
is*; CLI-Anything needs a human to write a harness over it. auto-cli is
that bridge — it runs the recon, classifies what it found, and forges a
registry-ready harness skeleton with the evidence attached.

```
target app ──► REA probes ──► findings ──► surface classifier ──► scaffold ──► validator
 (no API)      (evidence)     (provenance)   (3 flavors)        (CLI-Anything   (CONTRIBUTING
                                                               layout)         rules)
```

## Commands at a glance

| Command | What it does |
| --- | --- |
| `doctor` | check REA is reachable |
| `probe` | run REA recon (`--target` app path or `--url` website) → `evidence-bundle.json` |
| `surface` | classify a bundle into a harness plan (flavor + commands + risks) |
| `scaffold` | generate the CLI-Anything harness skeleton from a bundle |
| `verify` | prove findings against the **live** app; patches constants, writes `VERIFICATION.md` |
| `drive` | attach to a running app's Inspector, exercise IPC channels, implement `send` |
| `complete` | generate the domain surface (table groups, endpoint commands) from verified evidence |
| `gui` | Windows GUI driving via UIA: tree capture + gated actions + scaffold upgrade |
| `auto` | all of the above in one shot: recon → scaffold → verify → drive → complete → validate |
| `validate` | check a harness against CLI-Anything CONTRIBUTING rules |

Everything is deterministic and offline-capable except the probes themselves;
REA failures are recorded as limitations, never crashes.

## Getting started

Three pieces, one chain. Steps 1-2 give you the static pipeline; step 3 is a
real run; step 4 adds the Windows GUI leg.

**1. REA — the recon engine** (Node 22.19+/24.11+)

```bash
npm install -g rea-agents
```

**2. Auto-CLI — the pipeline** (Python 3.10+, stdlib only)

```bash
pip install git+https://github.com/Northernator/Auto-CLI.git
auto-cli doctor          # REA ok: 6.7.0
```

**3. One pipeline run** — any SQLite database works as a target:

```bash
# a target you can create in one line
python -c "import sqlite3; c=sqlite3.connect('library.db'); \
c.executescript('CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT, author TEXT); \
INSERT INTO books(title,author) VALUES (\'Dune\',\'Herbert\');'); c.commit()"

# CLI-Anything's repl skin (the generated harness ships an unmodified copy)
curl -O https://raw.githubusercontent.com/HKUDS/CLI-Anything/main/rms/agent-harness/cli_anything/rms/utils/repl_skin.py

auto-cli pipeline --target library.db --name library --out out --repl-skin repl_skin.py
```

Expected tail: `result: OK` — with the flavor classified, a registry-ready
harness under `out/library/`, and its own unit tests passing.

**4. (Windows only) the GUI leg** — drives apps with no API. The provider
lives in its own repo and installs into a REA *source checkout*, because it
patches REA's provider registry:

```bash
git clone https://github.com/morluto/rea.git && cd rea
git clone https://github.com/Northernator/rea-windows.git ../rea-windows
../rea-windows/install.sh .

pip install uiautomation pillow        # the provider's dependencies

export REA_BIN="$PWD/scripts/rea.mjs"                  # use the patched checkout
export REA_WINDOWS_PYTHON="$(python -c 'import sys;print(sys.executable)')"

auto-cli gui --process Notepad --tree
```

You should see `[gui] backend: REA native-windows provider`. Without this
step the `gui` command still works, using the built-in UIA driver — the REA
backend just upgrades the evidence to a real REA envelope.

REA is located via `rea` on PATH, `$REA_BIN`, or an `npx` fallback.

## Usage

One shot:

```bash
auto-cli pipeline --target /path/to/app --name my-app --out forge-out
```

Or step by step:

```bash
auto-cli probe    --target /path/to/app --out forge-out     # REA probes -> evidence-bundle.json
auto-cli surface  --bundle forge-out/evidence-bundle.json   # classify -> harness plan
auto-cli scaffold --bundle forge-out/evidence-bundle.json --name my-app --out forge-out
auto-cli validate --dir forge-out/my-app/agent-harness      # CONTRIBUTING.md checks
```

Options: `--probe <cmd>` (repeatable) to restrict REA commands,
`--repl-skin <path>` to drop the plugin's unmodified `repl_skin.py` into the
generated harness (CONTRIBUTING.md requires a real copy), `--author` for the
registry entry.

## Pointing it at things

**Desktop apps — pass a path.** Any of these work:

```bash
# extracted Electron app directory (app.asar or .js files)
auto-cli pipeline --target ~/AppData/Local/Programs/SomeApp/resources/app --name some-app

# the .asar itself
auto-cli pipeline --target /Applications/SomeApp.app/Contents/Resources/app.asar --name some-app

# a macOS .app bundle
auto-cli pipeline --target /Applications/SomeApp.app --name some-app

# a Windows install directory or the .exe (native binary recon)
auto-cli pipeline --target "C:/Program Files/SomeApp" --name some-app
```

The forge picks probes from what it sees: `analyze` always,
`analyze-javascript-application` when the target contains JS/ASAR,
`inspect-sqlite-database` when it finds a `.db`/`.sqlite` file. Force any REA
command with `--probe` (repeatable), e.g. `--probe inspect-macho`.

**Websites — pass `--url`.** REA drives a real Chrome-family browser:

```bash
auto-cli pipeline --url https://example.com --name example-site
```

The chain: launch browser → navigate → settle → capture DOM + network (with
response bodies) → `export-web-scripts` → `analyze-javascript-application` on
the exported scripts. Browser auto-detects Chrome/Edge (override with
`--browser PATH` or `$HARNESS_FORGE_BROWSER`); tune the settle time with
`--wait-ms`.

For WebMCP discovery you need a running debug browser:

```bash
# terminal 1: chrome --remote-debugging-port=9222
auto-cli pipeline --url https://some-app.com --cdp http://127.0.0.1:9222 --name some-app
```

With `--cdp`, the forge also runs `list-browser-targets`,
`discover-webmcp-tools`, and `analyze-web-bundle` against the matching target
— WebMCP findings map to the mcp-face flavor.

**What "found nothing" looks like:** a static page (no API, no bundles worth
analyzing) honestly yields flavor `unknown` with a todo to run deeper probes.
That is the tool working, not failing.

## What the classifier looks for

| Flavor | REA signal | Generated commands |
| --- | --- | --- |
| local-server | loopback endpoints (`127.0.0.1:PORT`), Electron IPC channels, context-bridge APIs | `get`, `endpoints`, `channels`, `send` |
| database | SQLite files in the app bundle | `info`, `schema`, `backup`, `query`, `export` |
| mcp-face | WebMCP tools / MCP servers | `tools`, `call` |
| scripting-runtime | embedded Python/JS consoles | `eval` (guarded) |

Databases trigger the Rekordbox rule: **backup before any write**. The
generated backend opens SQLite read-only (`mode=ro`), rejects non-SELECT
statements and multi-statement input, and `backup` refuses to clobber. HTTP
`get` is origin-scoped: loopback always, remote hosts only if recon
discovered them. `send` (IPC) starts as a stub and gets a real
implementation from `drive` once a channel is confirmed live.

## What it generates

```
<name>/agent-harness/            # standard CLI-Anything layout
  <NAME>.md                       # SOP doc
  setup.py                        # entry point cli-anything-<name>
  cli_anything/<module>/
    <module>_cli.py               # Click CLI, --json on every command
    core/backend.py               # constants baked in from recon + guards
    skills/SKILL.md               # agent-facing manual
    tests/test_core.py            # 8 unit tests, no backend needed
    utils/repl_skin.py            # placeholder unless --repl-skin given
  FORGE-REPORT.md                 # audit trail: probes, findings, risks, todos
  registry-entry.json             # registry.json fragment with all fields
```

## Closing the loop: verify against the live app

Static recon says an endpoint exists. `verify` asks the endpoint:

```bash
# with the target app actually running
auto-cli verify --bundle forge-out/evidence-bundle.json --dir forge-out/my-app/agent-harness
```

Per finding it records `verified` / `corrected` / `failed` / `unverifiable`,
patches corrected constants into the generated backend, and writes
`VERIFICATION.md` + `verification.json`. IPC channels are honestly marked
unverifiable (static observation only). 502/503/504 on loopback is flagged as
possible proxy interception, not app behavior.

### The full agent loop

```bash
auto-cli pipeline --target ./my-app --name my-app        # recon + scaffold + validate
# start the app's backend...
auto-cli verify --bundle forge-out/evidence-bundle.json \
    --dir forge-out/my-app/agent-harness                      # prove findings live
# agent (or you) reads VERIFICATION.md, adds domain commands + workflows + tests
auto-cli validate --dir forge-out/my-app/agent-harness   # final gate
```

### Experiment: how far does an agent get?

Ran on a demo app (Electron-style JS + SQLite library + live REST backend on
the port REA found in its source). Result: **the agent completed the harness.**

| Step | Who | Outcome |
| --- | --- | --- |
| recon (REA probes) | tool | endpoint + 4 IPC channels + DB found, statically |
| scaffold | tool | full CLI-Anything layout, working generic commands |
| verification | tool | endpoint proven live: `200 application/json, keys=['tracks']`; DB schema verified; constants patched |
| domain command `tracks` | agent | added from the verified response shape |
| SKILL.md workflows | agent | written from verified shapes (incl. "bare origin 404s") |
| regression tests | agent | 2 added (verified-shape parse, non-200 rejection) |
| final validation | tool | 9 commands, 10/10 tests, `result: OK` |

The agent's one bug (forgot to parse the response body) was caught by its own
test in one iteration. What still needs a human: wiring IPC `send` (needs a
live app session), choosing the command surface (product judgment), and PR
review. Verdict: for local-server and database flavors, the loop is closed
enough that an agent finishes the harness with the verification report in
hand.

## Driving live sessions: the `drive` command

That last gap — IPC `send` — needs a live app session. REA observes but never
mutates a target (its own contract), so the split is: **the forge drives, REA
witnesses.**

```bash
# app already running with an inspector (Electron: --remote-debugging-port,
# Node: --inspect=9229)
auto-cli drive --bundle forge-out/evidence-bundle.json \
    --dir forge-out/my-app/agent-harness \
    --inspector http://127.0.0.1:9229

# or let the forge launch it
auto-cli drive --bundle ... --dir ... \
    --launch "node --inspect=9229 app.js" --cwd /path/to/app
```

What happens: REA lists and observes the live runtime (witnessed evidence),
the forge evaluates inventory + send probes against the target, every channel
that responds is recorded with its actual response, and the scaffold is
upgraded — `send` gets a real implementation (a dependency-free Node CDP
client at `utils/inspector_send.mjs`), `LIVE_CHANNELS`/`INSPECTOR_ENDPOINT`
are baked into the backend, tests and SKILL.md are updated, and
`DRIVE-REPORT.md` records the session. Channels that never respond stay
unsendable (exit 2).

Live proof on the demo app: REA observed the runtime, the inventory probe
returned `["library:load","library:export"]`, both channels were driven
(`library:load` → `{"ok":true,"tracks":1}`), and the generated
`cli-anything-demo-dj send library:load` returned that same response from the
running app.

## GUI-only apps on Windows: the `gui` command

The GUI leg has two backends. **REA** (preferred) delegates to the
`native-windows` provider from
[rea-windows](https://github.com/Northernator/rea-windows), so GUI evidence
is a real REA evidence envelope. The **built-in** UIA driver is the standalone
fallback for hosts without the provider. `--backend auto|rea|local` forces
one; auto probes REA once and falls back silently.

```bash
# auto: REA when available, built-in otherwise
auto-cli gui --process Notepad --tree

# force one backend
auto-cli gui --backend rea  --process Notepad --window-id 787822 --tree
auto-cli gui --backend local --process Notepad --tree
```

To use the REA backend, install the provider into a REA checkout
(`./install.sh /path/to/rea` from the rea-windows repo), then point the forge
at it:

```bash
export REA_BIN=/path/to/rea/scripts/rea.mjs        # node-script entries work
export REA_WINDOWS_PYTHON=$(which python)          # interpreter with uiautomation + Pillow
```

REA's native-UI observation is macOS-only, so on Windows the forge drives UI
Automation itself (`pip install uiautomation`, or `pip install
auto-cli[gui]`):

```bash
# read-only: capture the actionable tree
auto-cli gui --process Notepad --tree

# drive it (every action needs --yes; destructive paths need --allow-destructive)
auto-cli gui --title "Untitled" --yes \
    --set-text "PaneControl > Text editor=hello" \
    --invoke "PaneControl > PaneControl > Headings" \
    --dir forge-out/my-app/agent-harness        # upgrade the scaffold too
```

What it captures: every UIA node with its patterns (Invoke / Value /
ExpandCollapse / SelectionItem / Toggle), menu paths, and window screenshots
as action evidence. Paths accept any unique suffix, so callers don't track
window titles.

Safety model: `tree`/`menus` are read-only; every driving action requires
`--yes`; paths matching save/close/delete/exit/format are refused even with
`--yes` unless `--allow-destructive`.

The scaffold gets a `ui` command group backed by a self-contained
`utils/uia_runner.py` copied into the harness (no auto_cli dependency at
runtime): `ui-tree`, `ui-menus`, `ui-invoke`, `ui-select`, `ui-set-text` —
all with the same gates.

And it's wired into `auto`:

```bash
auto-cli auto --target "C:/Windows/System32/notepad.exe" --name notepad \
    --gui-process Notepad --gui-title "Untitled"
```

Live run on a fresh untitled Notepad: REA found nothing useful (correct — a
native GUI binary has no static automation surface), the GUI leg captured 51
nodes / 8 menu paths, the scaffold was upgraded, `result: OK`, and the
generated `ui-set-text` wrote text into the live window.

### Platform boundaries (measured, not guessed)

- **GUI driving is now built for Windows.** The forge drives UIAutomation
  directly (REA's native-UI leg is macOS-only), with before/after evidence and
  screenshots per action. `gui` command + `--gui-*` flags on `auto`.
- **`capture-process` is unavailable on native Windows** — REA's PTY adapter
  requires Linux/WSL/macOS. Recorded as a failed probe with REA's own message,
  never a crash.
- **REA never mutates targets** — no launch/resume/evaluate/pause. All driving
  is done by auto-cli itself; REA supplies the witnessed evidence.
- Proxy environments: 502/503/504 on loopback is flagged as proxy-suspect, not
  app behavior.

## Fully automatic: the `auto` command

One command, no agent in the loop:

```bash
auto-cli auto --target ./my-app --name my-app \
    --inspector http://127.0.0.1:9229 \
    --repl-skin /path/to/plugin/repl_skin.py --repair
```

Chain: recon → scaffold → (launch app if `--launch` given) → verify → drive →
**complete** → validate (+ one repair pass with `--repair`). Works for
`--target` and `--url`.

`complete` is the piece that removes the agent: it generates the domain
surface from *verified evidence only* — one command group per database table
(`list`/`get`/`search`/`add`/`update`/`delete`), one command per verified
endpoint, tests from observed shapes, and a SKILL.md documenting all of it.
Nothing is guessed: an endpoint that failed verification gets no command.

Safety is structural: reads use a read-only connection, writes require `--yes`
and take a timestamped backup first, identifiers are quoted, payloads are
validated against the live schema.

### What `auto` produced on the demo app, unattended

```
verify:  {'unverifiable': 5, 'verified': 2, 'failed': 1}
drive:   ['library:export', 'library:load'] confirmed live
table groups: tracks, playlists, playlist_tracks
endpoint commands: library-tracks
result: OK
```

And the generated commands work against the live app: `tracks list`,
`tracks search Solar`, `library-tracks` (verified endpoint, parsed JSON),
`send library:export --message '{"format":"m3u"}'` (live channel),
`tracks add --yes` (backup taken, row inserted). `add` without `--yes` exits 2.

## What it deliberately does NOT do

- **It does not auto-write your harness.** CLI-Anything is hand-written by
  design; this produces a compliant skeleton plus a report of what a human
  must verify. The `FORGE-REPORT.md` lists every finding with provenance
  (REA command, artifact sha256, source location) and the open questions.
- **It does not run the target app unattended.** Recon is static unless you
  give it `--url`/`--inspector`/`--launch` and a live target to drive.
- **It does not benchmark agents.** No success-rate claims.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

Layout: `rea_client` (locate/drive REA), `evidence` (envelope → findings),
`surface` (findings → plan), `scaffold` (plan → files), `verify` (live
endpoint/DB checks), `drive` (live-session channel driving + scaffold
upgrade), `complete` (evidence-driven domain-surface generation), `validate`
(CONTRIBUTING.md rules). REA capability failures (e.g. a probe needing a
newer Node, PTY capture on Windows, macOS-only UI observation) are recorded
as limitations, never crashes.
