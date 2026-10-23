# Optional terminal walkthrough (~75s) — library quickstart, Windows

The README already includes a [24-second Notepad demo](notepad-demo.mp4)
and [GIF](notepad-demo.gif), built from real app captures and command output.
This script is for an additional terminal walkthrough of the SQLite library
example. It complements the existing video; it is not a missing release asset.

## Before recording

Run the [full PowerShell quickstart](../README.md#copy-and-paste-quickstart-windows-powershell)
once so that the isolated environment, REA, demo database, and `repl_skin.py`
are ready. From the directory where you ran that block, set up the recording
session explicitly:

```powershell
$demoPreviousReaBin = $env:REA_BIN
Push-Location .\auto-cli-demo
$demoPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
$demoLibrary = Join-Path $PWD '.venv\Scripts\cli-anything-library.exe'
$env:REA_BIN = (Resolve-Path .tools/node_modules/rea-agents/scripts/rea.mjs).Path
```

The commands below use `terminal-out` as a separate output directory from the
quickstart's `out`. For a fresh generation in the recording, choose an unused
output directory. Record the PowerShell window with your usual screen recorder.
Keep installation and preparation outside the clip.

## Recording sequence

| t | Show / type | Expect |
|---|---|---|
| 0-5s | `# auto-cli: point it at software, get an agent-native CLI` | — |
| 5-12s | `& $demoPython -m auto_cli doctor` | REA reachable |
| 12-18s | `Get-ChildItem library-demo` | `library.db` (no API, just a SQLite file) |
| 18-45s | `& $demoPython -m auto_cli auto --target library-demo --name library --out terminal-out --repl-skin repl_skin.py` | recon → scaffold → verify → complete → validate; **`result: OK`** (pause 2s) |
| 45-52s | `& $demoPython -m pip install -e ./terminal-out/library/agent-harness -q` | generated CLI installed in the same environment |
| 52-60s | `& $demoLibrary --json books search Dune` | JSON row with Dune |
| 60-68s | `& $demoLibrary export books books.csv --format csv`, then `Get-Content books.csv` | both books |
| 68-75s | `& $demoPython -m auto_cli validate --dir terminal-out/library/agent-harness` | finish on **`result: OK`** |

Stop and investigate if a command fails. The timestamps are an editing guide,
not a runtime promise. Show actual output, and disclose any cuts or speed changes,
especially during the `auto` step.

## After recording

```powershell
$env:REA_BIN = $demoPreviousReaBin
Pop-Location
```
