# make_shortcuts.ps1 - creates clickable launchers for the Pips.
#
# Adds two shortcuts (each runs `pythonw.exe app.py`, so no console window):
#   - "Pips.lnk"  on the Desktop
#   - "Pips.lnk"  in the Start Menu (searchable)
#
# Use these to (re)launch the widget by hand - e.g. if the login autostart
# (install_autostart.ps1) did not fire, or if you closed the widget.

# Run:     powershell -ExecutionPolicy Bypass -File make_shortcuts.ps1
# Remove:  powershell -ExecutionPolicy Bypass -File make_shortcuts.ps1 -Remove

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$appPy = Join-Path $here "app.py"
$icon  = Join-Path $here "assets\widget.ico"
$name  = "Pips.lnk"

# Desktop + Start Menu programs folder (per-user).
$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop'))  $name),
    (Join-Path ([Environment]::GetFolderPath('Programs')) $name)
)

if ($Remove) {
    foreach ($lnk in $targets) {
        if (Test-Path -LiteralPath $lnk) {
            Remove-Item -LiteralPath $lnk -Force
            Write-Host "Removed: $lnk"
        } else {
            Write-Host "Not found (skipped): $lnk"
        }
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $appPy)) {
    throw "app.py not found next to make_shortcuts.ps1 at $appPy"
}

# Resolve pythonw.exe (falls back to bare name if not resolvable on PATH).
$pythonw = Join-Path $here "runtime\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    try {
        $pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
    } catch {
        $pythonw = "pythonw.exe"
    }
}

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in $targets) {
    $shortcut = $shell.CreateShortcut($lnk)
    $shortcut.TargetPath       = $pythonw
    $shortcut.Arguments        = "`"$appPy`""
    $shortcut.WorkingDirectory = $here
    if (Test-Path -LiteralPath $icon) { $shortcut.IconLocation = "$icon,0" }
    $shortcut.Description      = "Launch the Pips"
    $shortcut.Save()
    Write-Host "Created: $lnk"
}

Write-Host "Tip: right-click the Desktop shortcut -> Pin to taskbar / Pin to Start to keep it one click away."
