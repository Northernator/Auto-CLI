# asciinema demo script (~75s) — library quickstart, Windows
Record: `asciinema rec auto-cli.cast --idle-time-limit 2` (native Windows has no asciinema; use WSL driving powershell.exe or record with a Windows recorder such as Terminalizer). Prep beforehand: venv and REA already installed, demo DB created, repl_skin.py downloaded. The clip should show the tool, not installers.

| t | Show / type | Expect |
|---|---|---|
| 0-5s | `# auto-cli: point it at software, get an agent-native CLI` | — |
| 5-12s | `python -m auto_cli doctor` | REA reachable |
| 12-18s | `ls library-demo` | `library.db` (no API, just a SQLite file) |
| 18-45s | `python -m auto_cli auto --target library-demo --name library --out out --repl-skin repl_skin.py` | recon → scaffold → complete → validate; **`result: OK`** (pause 2s) |
| 45-52s | `pip install -e ./out/library/agent-harness -q` | — |
| 52-60s | `cli-anything-library --json books search Dune` | JSON row with Dune |
| 60-68s | `cli-anything-library export books books.csv --format csv; cat books.csv` | both books |
| 68-75s | `python -m auto_cli validate --dir out/library/agent-harness` | finish on **`result: OK`** |

Note: don't speed up the `auto` step without saying so in the caption.
