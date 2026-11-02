# setup_diary_task.ps1 - registers a Windows Task Scheduler job that runs the
# daily diary pipeline (generate + PDF + threaded email) every day at 22:00.
#
# Run this once:  powershell -ExecutionPolicy Bypass -File setup_diary_task.ps1
# Remove with:    Unregister-ScheduledTask -TaskName "ProductivityWidgetDiary" -Confirm:$false

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "ProductivityWidgetDiary"
$runScript = Join-Path $here "run_diary_daily.ps1"

if (-not (Test-Path -LiteralPath $runScript)) {
    throw "run_diary_daily.ps1 not found next to setup_diary_task.ps1 at $runScript"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`""

$trigger = New-ScheduledTaskTrigger -Daily -At 22:00

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
    -Description "Daily productivity diary: generate entry + PDF, email the diary every day at 22:00 (threaded)." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$taskName' for every day at 22:00."
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Run now with: Start-ScheduledTask -TaskName '$taskName'"
