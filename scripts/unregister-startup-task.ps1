$ErrorActionPreference = "Stop"

$TaskName = "Codex Commander Telegram Poller"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Unregistered scheduled task: $TaskName"
