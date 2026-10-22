# Notepad documentation demo

A silent 24-second demo built from a real Windows Notepad acceptance run. It shows Auto CLI generating a command surface, the generated CLI writing release notes, and the generated read command returning the same text.

`capture/` contains the unchanged application screenshots, sanitized command transcript and result. The video cuts between real captures; setup and build waits are shortened. Visible commands are excerpts. Use the repository README's complete PowerShell quickstart to repeat the task.

The output files are [`../../docs/notepad-demo.gif`](../../docs/notepad-demo.gif) and [`../../docs/notepad-demo.mp4`](../../docs/notepad-demo.mp4).

## Render

This folder contains documentation authoring tools only. Node.js 22+, a working FFmpeg installation (including FFprobe) and Chromium are needed; these are not Auto CLI runtime dependencies.

```powershell
cd videos/auto-cli-notepad
npm run check
npm run render -- --quality high --fps 24 --output ../../docs/notepad-demo.mp4
ffmpeg -i ../../docs/notepad-demo.mp4 -filter_complex "fps=10,scale=1280:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3" -loop 0 ../../docs/notepad-demo.gif
```

HyperFrames is pinned to 0.8.186. GSAP and the licensed fonts are local assets, so the composition does not fetch media or fonts while rendering. Font license files are included beside the fonts.

`stage-capture.mjs` imports new acceptance artifacts from the repository's ignored `.test-artifacts/notepad-demo/` directory. It is not needed to render the checked-in captures.
