---
format: 1920x1080
duration: 24s
message: "Turn Windows Notepad into a repeatable command-line workflow"
arc: "A familiar app → generate → write → verify"
audience: "developers trying Auto CLI on Windows"
mode: autonomous
music: none
---

## Video direction

Code editorial preset: cream canvas, serif headline, precise mono command surface, real Notepad screenshots. One continuous, restrained split composition keeps the application visible. Plain cuts switch between the real before and after capture; there is no invented typing animation. Commands are clearly identified as excerpts, with complete runnable steps in the README. The footer explicitly discloses an edited capture and shortened setup/build waits.

## Frame 1 — Generate, write, verify

- status: animated
- src: compositions/frames/01-notepad.html
- duration: 24s
- poster: 16s
- transition_in: cut
- scene: A generated CLI drafts release notes in real Windows Notepad and reads the exact text back.
- asset_candidates: capture/notepad-before.png, capture/notepad-after.png, capture/commands.json, capture/result.json
- assets: capture/notepad-before.png, capture/notepad-after.png
- blueprint: compose
- motion_rules: waterfall-entry, stat-bars-and-fills

0–3s: "Your Windows app. Now a CLI." A real blank Notepad capture occupies the right side; a short left-side statement introduces drafting and verifying release notes.

3–8s: The command panel shows the generation command excerpt and real captured output: the Notepad window, 23 discovered UI nodes, and nine passing generated tests. Headline becomes "Generate the command surface". The application image remains the actual before capture.

8–15s: The panel shows the actual write command using `$note` and the captured `{ok, action, path}` result. At 10s a clean cut reveals the actual after capture. The headline becomes "Draft the release notes". The note's real text remains readable; no synthetic cursor or typing effect.

15–21s: Show the actual read-text command, the real note response excerpt, and an annotation that the returned text matched the requested note and was exported to release-notes.txt. The full underlying transcript and result stay in capture/ for review. Headline becomes "Read it back. Verify it.".

21–24s: Keep the real result capture and close with "Try it from the README." plus github.com/Northernator/Auto-CLI. Commands remain labeled excerpts. A progress hairline reaches the end. No audio, decorative effects, or invented performance claims.
