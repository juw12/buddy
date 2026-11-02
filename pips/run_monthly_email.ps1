# run_monthly_email.ps1 - generates and emails the monthly productivity roll-up.
# Invoked by Windows Task Scheduler (see setup_monthly_email_task.ps1) or run manually.

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

"[$(Get-Date -Format s)] START monthly via $py" | Out-File -Append -Encoding utf8 "$here\logs\scheduler.log"
& $py "$here\daily_email.py" --monthly *>> "$here\logs\scheduler.log"
"[$(Get-Date -Format s)] END monthly exit=$LASTEXITCODE" | Out-File -Append -Encoding utf8 "$here\logs\scheduler.log"
