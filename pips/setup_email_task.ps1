# setup_email_task.ps1 - registers a Windows Task Scheduler job that emails the
# daily productivity report. It runs Sunday-Thursday at 22:00, so the report is
# waiting in your inbox on weekday mornings.
#
# Run this once:  powershell -ExecutionPolicy Bypass -File setup_email_task.ps1
# Remove with:    Unregister-ScheduledTask -TaskName "ProductivityWidgetEmail" -Confirm:$false

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "ProductivityWidgetEmail"
$runScript = Join-Path $here "run_daily_email.ps1"

if (-not (Test-Path -LiteralPath $runScript)) {
    throw "run_daily_email.ps1 not found next to setup_email_task.ps1 at $runScript"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`""

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday `
    -At 22:00

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Emails the daily productivity report (Sun-Thu 22:00) so it is waiting on weekday mornings." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$taskName' for Sun-Thu at 22:00."
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Run now with: Start-ScheduledTask -TaskName '$taskName'"
