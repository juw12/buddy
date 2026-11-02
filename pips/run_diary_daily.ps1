# run_diary_daily.ps1 - runs the daily diary pipeline (generate + PDF + threaded email).
# Invoked by Windows Task Scheduler (see setup_diary_task.ps1) or run manually.

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here

$py = $null
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $py = $cmd.Source }
if (-not $py) {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    "[$(Get-Date -Format s)] FATAL python not found on PATH" | Out-File -Append -Encoding utf8 "$here\logs\scheduler.log"
    exit 1
}

"[$(Get-Date -Format s)] DIARY START via $py" | Out-File -Append -Encoding utf8 "$here\logs\scheduler.log"
& $py "$here\diary_daily.py" *>> "$here\logs\scheduler.log"
"[$(Get-Date -Format s)] DIARY END exit=$LASTEXITCODE" | Out-File -Append -Encoding utf8 "$here\logs\scheduler.log"
