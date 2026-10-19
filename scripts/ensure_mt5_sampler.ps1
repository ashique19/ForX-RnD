# Start scripts/mt5_spread_sampler.py detached unless a HEALTHY one is already running (single-instance guard).
# Repo-relative; no credentials (the sampler reads the gitignored .env.mt5). Safe to call repeatedly (logon + 15-min task).
# Healthy = process alive AND data\costs\mt5_sampler.heartbeat written within $StaleMin minutes. A live sampler with a
# stale/missing heartbeat (e.g. stuck writing 0 rows on a dead MT5 session) is stopped and replaced. The sampler only
# reads quotes/ticks; this guard never touches MT5 or any order.
param([int]$IntervalSec = 300, [int]$StaleMin = 20)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$costs = Join-Path $root 'data\costs'
$log = Join-Path $costs 'mt5_sampler.log'
$heart = Join-Path $costs 'mt5_sampler.heartbeat'
New-Item -ItemType Directory -Force $costs | Out-Null
function Write-GuardLog($msg) { Add-Content $log ((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + " UTC guard $msg") }
$mutex = New-Object System.Threading.Mutex($false, 'Local\ForX_mt5_sampler_guard')
if (-not $mutex.WaitOne(0)) { exit 0 }   # another guard instance is mid-check
try {
    $running = @(Get-CimInstance Win32_Process -Filter "name like 'python%'" | Where-Object { $_.CommandLine -match 'mt5_spread_sampler\.py' })
    if ($running.Count -gt 0) {
        $fresh = (Test-Path $heart) -and ((Get-Item $heart).LastWriteTime -gt (Get-Date).AddMinutes(-$StaleMin))
        $started = ($running | ForEach-Object { $_.CreationDate } | Sort-Object | Select-Object -First 1)
        $young = $started -and ($started -gt (Get-Date).AddMinutes(-$StaleMin))
        if ($fresh -or $young) { exit 0 }
        foreach ($p in $running) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-GuardLog ("stopped stale sampler PID(s) " + (($running | ForEach-Object { $_.ProcessId }) -join ',') + " (heartbeat older than $StaleMin min or missing)")
        Start-Sleep -Seconds 2
    }
    $py = Join-Path $root '.venv\Scripts\python.exe'
    $p = Start-Process -WindowStyle Hidden -PassThru -FilePath $py -ArgumentList '-u', 'scripts\mt5_spread_sampler.py', "$IntervalSec" `
        -WorkingDirectory $root -RedirectStandardOutput (Join-Path $costs 'mt5_sampler.out') -RedirectStandardError (Join-Path $costs 'mt5_sampler.err')
    Write-GuardLog "started sampler PID $($p.Id)"
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
