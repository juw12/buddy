# setup_monthly_email_task.ps1 - registers a Windows Task Scheduler job that emails the
# monthly productivity roll-up on the 1st of each month at 08:00 (reporting last month).
#
# Run this once:  powershell -ExecutionPolicy Bypass -File setup_monthly_email_task.ps1
# Remove with:    powershell -ExecutionPolicy Bypass -File setup_monthly_email_task.ps1 -Remove

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "ProductivityWidgetMonthlyEmail"
$runScript = Join-Path $here "run_monthly_email.ps1"

if ($Remove) {
    schtasks /Delete /TN $taskName /F
    Write-Host "Removed scheduled task '$taskName'."
    exit 0
}

if (-not (Test-Path -LiteralPath $runScript)) {
    throw "run_monthly_email.ps1 not found next to setup_monthly_email_task.ps1 at $runScript"
}

$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`""
schtasks /Create /TN $taskName /TR $action /SC MONTHLY /D 1 /ST 08:00 /F

Write-Host "Registered scheduled task '$taskName' for the 1st of each month at 08:00."
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Run now with: Start-ScheduledTask -TaskName '$taskName'"
