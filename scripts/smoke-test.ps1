param(
  [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'

Push-Location $Root
try {
  python -m py_compile .\commander.py .\dashboard.py
  python -m compileall -q .\commanderx
  python -m unittest discover -s .\tests
  python .\commander.py --local "/help" | Out-Null
  python .\commander.py --local "/projects" | Out-Null
  python .\commander.py --local "/queue" | Out-Null
  python .\commander.py --local "/memory" | Out-Null

  $dashboard = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*dashboard.py*' }
  if ($dashboard) {
    $response = Invoke-RestMethod http://127.0.0.1:8787/api/dashboard
    if (-not $response.projects) {
      throw 'Dashboard API did not return projects.'
    }
  }

  'Commander X smoke test passed.'
}
finally {
  Pop-Location
}
