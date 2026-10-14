# Start scripts/mt5_spread_sampler.py detached unless one is already running (single-instance guard).
# Repo-relative; no credentials (the sampler reads the gitignored .env.mt5). Safe to call repeatedly (logon + 15-min task).
param([int]$IntervalSec = 300)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'data\costs\mt5_sampler.log'
New-Item -ItemType Directory -Force (Join-Path $root 'data\costs') | Out-Null
$mutex = New-Object System.Threading.Mutex($false, 'Local\ForX_mt5_sampler_guard')
if (-not $mutex.WaitOne(0)) { exit 0 }   # another guard instance is mid-check
try {
    $running = Get-CimInstance Win32_Process -Filter "name like 'python%'" | Where-Object { $_.CommandLine -match 'mt5_spread_sampler\.py' }
    if ($running) { exit 0 }
    $py = Join-Path $root '.venv\Scripts\python.exe'
    $p = Start-Process -WindowStyle Hidden -PassThru -FilePath $py -ArgumentList '-u', 'scripts\mt5_spread_sampler.py', "$IntervalSec" `
        -WorkingDirectory $root -RedirectStandardOutput (Join-Path $root 'data\costs\mt5_sampler.out') -RedirectStandardError (Join-Path $root 'data\costs\mt5_sampler.err')
    Add-Content $log ((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + " UTC guard started sampler PID $($p.Id)")
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
