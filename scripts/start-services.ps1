param(
  [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Logs = Join-Path $Root 'logs'
$Archive = Join-Path $Logs 'archive'
New-Item -ItemType Directory -Force -Path $Logs, $Archive | Out-Null

function Archive-Log {
  param([string]$Name)
  $Path = Join-Path $Logs $Name
  if (Test-Path $Path) {
    $Item = Get-Item $Path
    if ($Item.Length -gt 0) {
      $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
      Move-Item -LiteralPath $Path -Destination (Join-Path $Archive "$($Item.BaseName)-$Stamp$($Item.Extension)") -Force
    }
  }
}

function Start-PythonService {
  param(
    [string]$Match,
    [string[]]$ServiceArgs,
    [string]$OutLog,
    [string]$ErrLog
  )

  $Existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*$Match*" }

  if ($Existing -and $Restart) {
    foreach ($Proc in $Existing) {
      Stop-Process -Id $Proc.ProcessId -Force
    }
    Start-Sleep -Seconds 1
    $Existing = @()
  }

  if ($Existing) {
    $Existing | ForEach-Object { Write-Host "$Match already running: PID $($_.ProcessId)" }
    return
  }

  Archive-Log $OutLog
  Archive-Log $ErrLog

  $Python = (Get-Command python).Source
  $Process = Start-Process -FilePath $Python -ArgumentList $ServiceArgs -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Logs $OutLog) `
    -RedirectStandardError (Join-Path $Logs $ErrLog) `
    -PassThru
  Write-Host "$Match started: PID $($Process.Id)"
}

Start-PythonService -Match 'commander.py --poll' -ServiceArgs @('commander.py', '--poll') -OutLog 'commander-service.out.log' -ErrLog 'commander-service.err.log'
Start-PythonService -Match 'dashboard.py' -ServiceArgs @('dashboard.py') -OutLog 'dashboard.out.log' -ErrLog 'dashboard.err.log'
