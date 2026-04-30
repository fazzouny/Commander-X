$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Existing = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match 'python' -and $_.CommandLine -like '*commander.py*--poll*'
}

if ($Existing) {
  Write-Host "Codex Commander is already running."
  exit 0
}

python .\commander.py --poll
