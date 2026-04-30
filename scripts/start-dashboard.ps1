$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Existing = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match 'python' -and $_.CommandLine -like '*dashboard.py*'
}

if ($Existing) {
  Write-Host "Commander X dashboard is already running."
  exit 0
}

python .\dashboard.py
