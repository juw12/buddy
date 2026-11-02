# install_autostart.ps1 - registers the Pips to start at login.
#
# Creates a shortcut in the current user's Startup folder that runs
# pythonw.exe "<project>\app.py" (with the project as working dir), so the
# widget launches quietly with no console after you sign in.
#
# Run this once:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# Remove it:      powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Remove

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$appPy     = Join-Path $here "app.py"
$shortName = "Pips.lnk"

# The per-user Startup folder (opens at log in).
$startup = [Environment]::GetFolderPath('Startup')
$lnk     = Join-Path $startup $shortName

if ($Remove) {
    if (Test-Path -LiteralPath $lnk) {
        Remove-Item -LiteralPath $lnk -Force
        Write-Host "Removed autostart shortcut: $lnk"
    } else {
        Write-Host "No autostart shortcut found at $lnk - nothing to remove."
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $appPy)) {
    throw "app.py not found next to install_autostart.ps1 at $appPy"
}

# Resolve pythonw.exe (falls back to the bare name if not resolvable on PATH).
$pythonw = Join-Path $here "runtime\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    try {
        $pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
    } catch {
        $pythonw = "pythonw.exe"
    }
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnk)
$shortcut.TargetPath       = $pythonw
$shortcut.Arguments        = "`"$appPy`""
$shortcut.WorkingDirectory = $here
$shortcut.Description      = "Launch the Pips at log in (no console)."
$shortcut.Save()

Write-Host "Created autostart shortcut: $lnk"
Write-Host "  -> $pythonw $appPy  (working dir: $here)"
Write-Host "Remove with: powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Remove"
