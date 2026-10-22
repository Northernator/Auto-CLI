# Recorded evidence

- `notepad-before.png`: unchanged 1955 × 1486 real Windows Notepad window, blank Text Editor before the generated command ran.
- `notepad-after.png`: unchanged 1955 × 1486 real Windows Notepad window with the complete release-note text, captured after the generated command ran.
- `commands.json`: actual command arguments, exit status and output from the acceptance run. Absolute checkout paths were replaced with `<repo>` and installation logs excluded.
- `result.json`: actual read-back result and successful equality/export assertions from the acceptance run.

The demo uses clean edits between these real captures. Setup and build waits are shortened. Visible commands are excerpts; the repository README and runnable example contain the full steps.
