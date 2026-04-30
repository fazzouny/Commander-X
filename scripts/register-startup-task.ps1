$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $Root "scripts\start-commander.ps1"
$TaskName = "Codex Commander Telegram Poller"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Runs Codex Commander Telegram polling at user logon." -Force | Out-Null
Write-Host "Registered scheduled task: $TaskName"
