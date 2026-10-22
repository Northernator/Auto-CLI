<#
Run the README quickstart first. From the same parent folder, copy and paste
this file into PowerShell, or run it if your script policy permits:

    .\examples\notepad-demo.ps1 -Workspace .\auto-cli-demo

Uses classic Windows Notepad. It opens a new window by default, generates a
CLI, writes a release checklist, reads it back through UI Automation, and
exports that retrieved text. The Notepad document remains open and unsaved.
An optional -WindowPid may target an existing empty Notepad window; nonempty
editors are refused. Modern Notepad can redirect to a different process and
expose different controls, so this example fails if it cannot identify the
requested window and a unique Value-capable editor.

No environment activation or execution-policy change is required when the
contents are pasted into PowerShell. The optional uiautomation dependency
is installed into the demo virtual environment, never globally.
#>
param(
    [string] $Workspace = (Join-Path $PWD 'auto-cli-demo'),
    [string] $Python = '',
    [int] $WindowPid = 0
)

$ErrorActionPreference = 'Stop'
function Invoke-NotepadNative {
    param([string] $Command, [Parameter(ValueFromRemainingArguments = $true)][string[]] $Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed (exit $LASTEXITCODE)" }
}

$Workspace = (Resolve-Path $Workspace).Path
if (-not $Python) { $Python = Join-Path $Workspace '.venv\Scripts\python.exe' }
if (-not (Test-Path $Python)) { throw 'Demo Python not found. Run the README quickstart first.' }
$Python = (Resolve-Path $Python).Path
$notepadExe = Join-Path $env:WINDIR 'System32\notepad.exe'
if (-not (Test-Path $notepadExe)) { throw 'Windows Notepad was not found.' }

Push-Location $Workspace
$previousReaBin = $env:REA_BIN
try {
    $localRea = '.tools\node_modules\rea-agents\scripts\rea.mjs'
    if (Test-Path $localRea) { $env:REA_BIN = (Resolve-Path $localRea).Path }
    Invoke-NotepadNative $Python -m pip install uiautomation
    if (-not (Test-Path repl_skin.py)) {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/HKUDS/CLI-Anything/main/rms/agent-harness/cli_anything/rms/utils/repl_skin.py' -OutFile repl_skin.py
    }

    if (-not $WindowPid) {
        $notepad = Start-Process -FilePath $notepadExe -PassThru
        $WindowPid = $notepad.Id
        try { $null = $notepad.WaitForInputIdle(10000) } catch {
            throw 'Notepad redirected to another process. This example requires classic Notepad; inspect the GUI tree for your installed version.'
        }
        Start-Sleep -Milliseconds 500
    }

    Write-Host "Generating a CLI for Notepad PID $WindowPid..."
    Invoke-NotepadNative $Python -m auto_cli auto --target $notepadExe --name notes --out out --repl-skin repl_skin.py --gui-process Notepad --gui-pid $WindowPid
    Invoke-NotepadNative $Python -m pip install --editable ./out/notes/agent-harness

    $tree = Invoke-NotepadNative $Python -m cli_anything.notes.notes_cli --json ui-tree | ConvertFrom-Json
    $editors = @($tree.nodes | Where-Object { $_.control_type -eq 'EditControl' -and $_.patterns -contains 'Value' })
    if ($editors.Count -ne 1) {
        throw "Expected one Value-capable EditControl; found $($editors.Count). Inspect ui-tree for this Notepad version."
    }
    # Drop the changing window title; the remaining path is a unique suffix.
    $editorPath = ($editors[0].path -split ' > ', 2)[1]
    if (-not $editorPath) { throw 'The editor has no usable UI path.' }
    $initial = Invoke-NotepadNative $Python -m cli_anything.notes.notes_cli --json ui-read-text $editorPath | ConvertFrom-Json
    if ($initial.text.Length -ne 0) { throw 'Refusing to replace a nonempty Notepad document. Open a fresh empty window.' }

    $note = "Release notes - Auto-CLI`r`n`r`n- Generate a CLI from app evidence.`r`n- Draft and verify notes in Windows Notepad.`r`n- Export the verified text to release-notes.txt.`r`n"
    Invoke-NotepadNative $Python -m cli_anything.notes.notes_cli ui-set-text $editorPath $note --yes
    $result = Invoke-NotepadNative $Python -m cli_anything.notes.notes_cli --json ui-read-text $editorPath | ConvertFrom-Json
    if (($result.text -replace "`r`n", "`n") -cne ($note -replace "`r`n", "`n")) {
        throw 'The UI readback does not match the requested note.'
    }

    # Export the actual readback; this does not invoke Notepad's Save command.
    $exportPath = Join-Path $Workspace 'release-notes.txt'
    $result.text | Set-Content -LiteralPath $exportPath -Encoding UTF8 -NoNewline
    $exported = Get-Content -LiteralPath $exportPath -Raw
    if (($exported -replace "`r`n", "`n") -cne ($note -replace "`r`n", "`n")) {
        throw 'The exported file does not match the UI readback.'
    }
    Write-Host "Verified Notepad UI readback and exported it to $exportPath"
    Write-Host 'The demo Notepad window is left open for inspection.'
}
finally {
    $env:REA_BIN = $previousReaBin
    Pop-Location
}
