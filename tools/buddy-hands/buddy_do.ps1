<#
Ask the running Buddy to look, click or type on screen for an AI agent, and print Buddy's answer (one JSON line).

  buddy_do.ps1 -Agent tester -Do look  -Window Notepad
  buddy_do.ps1 -Agent tester -Do click -Window Notepad -Target Save
  buddy_do.ps1 -Agent tester -Do type  -Window "Save As" -Target "File name" -Text notes
  buddy_do.ps1 -Agent tester -Do click -X 900 -Y 500

Exit code: 0 done, 3 not done (the answer says why), 1 Buddy is not running, 2 Buddy.exe was not found.
#>
param(
    [Parameter(Mandatory = $true)][string]$Agent,
    [Parameter(Mandatory = $true)][ValidateSet("look", "click", "type")][string]$Do,
    [string]$Window = "",
    [string]$Target = "",
    [string]$Text = "",
    [Nullable[int]]$X = $null,
    [Nullable[int]]$Y = $null,
    [string]$Team = "",
    [string]$Buddy = ""
)
$ErrorActionPreference = "Stop"

if (-not $Buddy) {
    $running = Get-Process Buddy -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
    if ($running) { $Buddy = $running.Path } elseif ($env:BUDDY_EXE) { $Buddy = $env:BUDDY_EXE }
}
if (-not $Buddy -or -not (Test-Path $Buddy)) {
    '{"ok": false, "did": "Buddy is not running, so Buddy.exe was not found. Start Buddy first."}'
    exit 2
}

$request = [ordered]@{ agent = $Agent; do = $Do }
if ($Team) { $request.team = $Team }
if ($Window) { $request.window = $Window }
if ($Target) { $request.target = $Target }
if ($Text) { $request.text = $Text }
if ($null -ne $X -and $null -ne $Y) { $request.x = [int]$X; $request.y = [int]$Y }
$json = $request | ConvertTo-Json -Compress

# One argument for a Windows program: a quote gets a backslash, and backslashes in front of a quote are doubled
$escaped = [regex]::Replace($json, '(\\*)"', '$1$1\"')
$escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
$answer = Join-Path ([System.IO.Path]::GetTempPath()) ("buddy-answer-" + [guid]::NewGuid().ToString("N") + ".json")
$process = Start-Process -FilePath $Buddy -ArgumentList @("--crew", ('"' + $escaped + '"'), "--out", ('"' + $answer + '"')) -Wait -PassThru -WindowStyle Hidden
if (Test-Path $answer) {
    Get-Content $answer -Raw -Encoding UTF8
    Remove-Item $answer -ErrorAction SilentlyContinue
} else {
    '{"ok": false, "did": "Buddy gave no answer (exit code ' + $process.ExitCode + ')."}'
}
exit $process.ExitCode
