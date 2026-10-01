# START_DESK.ps1 â€” reliable ForX Decision desk launcher
# Usage:
#   .\START_DESK.ps1           # free ports, start API+Desk, wait health, open browser
#   .\START_DESK.ps1 -Stop     # stop listeners on 8000/5173
#   .\START_DESK.ps1 -FromBat  # called from RUN_UI.bat (same as default)
param(
    [switch]$Stop,
    [switch]$FromBat,
    [switch]$NoBrowser,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$DataDir = Join-Path $Root 'data'
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
$LogFile = Join-Path $DataDir 'desk_start.log'
$ApiOut  = Join-Path $DataDir 'api_stdout.log'
$DeskOut = Join-Path $DataDir 'desk_stdout.log'
$ApiErr  = Join-Path $DataDir 'api_stderr.log'
$DeskErr = Join-Path $DataDir 'desk_stderr.log'

function Write-DeskLog([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-ListeningPids([int]$Port) {
    $pids = @()
    netstat -ano | Select-String ":$Port\s+.*LISTENING" | ForEach-Object {
        $parts = ($_.Line -split '\s+') | Where-Object { $_ -ne '' }
        if ($parts.Count -ge 5) {
            $pidVal = [int]$parts[-1]
            if ($pidVal -gt 0) { $pids += $pidVal }
        }
    }
    return $pids | Select-Object -Unique
}

function Stop-Port([int]$Port) {
    $pids = Get-ListeningPids -Port $Port
    foreach ($procId in $pids) {
        Write-DeskLog "Freeing port $Port (PID $procId) and process tree"
        # /T kills child+parent stubs (Windows venv python often spawns base interpreter)
        & taskkill /F /T /PID $procId 2>$null | Out-Null
        try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
    }
    # Also sweep leftover uvicorn/vite started from this repo (orphans after unclean kill)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -match '^(python|pythonw)\.exe$' -and $_.CommandLine -match 'uvicorn api\.main:app') -or
            ($_.Name -eq 'node.exe' -and $_.CommandLine -match 'forex-lab\\desk\\node_modules.*vite')
        } |
        ForEach-Object {
            Write-DeskLog "Sweep orphan $($_.Name) PID $($_.ProcessId)"
            & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null
        }
    if ($pids.Count -gt 0) { Start-Sleep -Seconds 1 }
}

function Wait-PortFree([int]$Port, [int]$Seconds = 15) {
    for ($i = 1; $i -le $Seconds; $i++) {
        $left = @(Get-ListeningPids -Port $Port)
        if ($left.Count -eq 0) {
            Write-DeskLog "Port $Port is free"
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    $left = @(Get-ListeningPids -Port $Port)
    if ($left.Count -eq 0) { return $true }
    Write-DeskLog ("WARNING: port {0} still held by PIDs {1}" -f $Port, ($left -join ','))
    return $false
}

function Test-PortListening([int]$Port) {
    return (@(Get-ListeningPids -Port $Port).Count -gt 0)
}

function Enter-DeskLock {
    $lockPath = Join-Path $DataDir 'desk_start.lock'
    $myPid = $PID
    if (Test-Path $lockPath) {
        try {
            $raw = Get-Content $lockPath -Raw -ErrorAction SilentlyContinue
            $other = 0
            if ($raw -match '(\d+)') { $other = [int]$Matches[1] }
            if ($other -gt 0 -and $other -ne $myPid) {
                $alive = Get-Process -Id $other -ErrorAction SilentlyContinue
                if ($alive) {
                    Write-DeskLog "ERROR: another START_DESK is running (PID $other). Aborting to avoid port race."
                    Write-Host "START_DESK already running (PID $other). Use -Stop first, or wait."
                    exit 2
                }
            }
        } catch {}
    }
    Set-Content -Path $lockPath -Value ("{0}`n{1}" -f $myPid, (Get-Date -Format 'o')) -Encoding UTF8
    Write-DeskLog "Acquired desk_start.lock PID=$myPid"
    return $lockPath
}

function Exit-DeskLock([string]$LockPath) {
    if ($LockPath -and (Test-Path $LockPath)) {
        try {
            $raw = Get-Content $LockPath -Raw -ErrorAction SilentlyContinue
            if ($raw -match ("^{0}\b" -f $PID)) {
                Remove-Item -Force $LockPath -ErrorAction SilentlyContinue
                Write-DeskLog "Released desk_start.lock"
            }
        } catch {}
    }
}

function Test-Healthy {
    try {
        $h = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2
        $u = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2
        return ($h.Content -match '"ok"\s*:\s*true' -or $h.Content -match 'ok') -and ($u.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Clear-VitePrebundle {
    $viteCache = Join-Path $Root 'desk\node_modules\.vite'
    if (Test-Path $viteCache) {
        Write-DeskLog "Clearing stale Vite prebundle (CandlestickSeries fix): $viteCache"
        Remove-Item -Recurse -Force $viteCache -ErrorAction SilentlyContinue
    }
}

if ($Stop) {
    Write-DeskLog '===== STOP requested ====='
    Stop-Port 8000
    Stop-Port 5173
    Write-DeskLog 'Stopped listeners on 8000/5173'
    exit 0
}

Write-DeskLog '===== START_DESK begin ====='

$lockPath = Enter-DeskLock
try {

if (-not $Force -and (Test-Healthy)) {
    Write-DeskLog 'Already healthy on :8000 and :5173 - skipping restart (pass -Force to recycle)'
    Write-Host 'ForX Decision desk already UP'
    Write-Host '  API   http://127.0.0.1:8000/health'
    Write-Host '  Desk  http://127.0.0.1:5173'
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173' }
    exit 0
}

Clear-VitePrebundle
Write-DeskLog "Root=$Root"

# Always clear stale listeners first (cold-boot / unclean shutdown)
Stop-Port 8000
Stop-Port 5173
[void](Wait-PortFree -Port 8000 -Seconds 20)
[void](Wait-PortFree -Port 5173 -Seconds 20)

$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $Python = $venvPython
    Write-DeskLog "Using venv python: $Python"
} else {
    $Python = 'python'
    Write-DeskLog 'WARNING: .venv missing â€” using PATH python'
}

try {
    & $Python -c "import fastapi,uvicorn" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'fastapi/uvicorn import failed' }
} catch {
    Write-DeskLog "ERROR: cannot import fastapi/uvicorn: $_"
    Write-Host 'Run INSTALL.bat first, then retry.'
    exit 1
}

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-DeskLog 'ERROR: npm not found on PATH'
    Write-Host 'Install Node 20+ from https://nodejs.org/'
    exit 1
}

$nodeModules = Join-Path $Root 'desk\node_modules'
if (-not (Test-Path $nodeModules)) {
    Write-DeskLog 'npm install (desk/node_modules missing)'
    Push-Location (Join-Path $Root 'desk')
    try {
        & npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install exit $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

# Clear Vite prebundle every cold start (stale cache drops CandlestickSeries export after boot/shutdown)
$viteCache = Join-Path $Root 'desk\node_modules\.vite'
if (Test-Path $viteCache) {
    Write-DeskLog 'Clearing stale Vite prebundle cache (desk\node_modules\.vite)'
    Remove-Item -LiteralPath $viteCache -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $viteCache) {
        Write-DeskLog 'WARNING: could not fully remove .vite cache; continuing'
    } else {
        Write-DeskLog 'Vite prebundle cache cleared'
    }
} else {
    Write-DeskLog 'Vite prebundle cache already absent'
}

# Truncate run logs
'' | Set-Content -Path $ApiOut -Encoding UTF8
'' | Set-Content -Path $ApiErr -Encoding UTF8
'' | Set-Content -Path $DeskOut -Encoding UTF8
'' | Set-Content -Path $DeskErr -Encoding UTF8

# Start API (no shell nesting): redirect stdout/stderr to files; keep a console via cmd title window optional
# Tip Volume self-heal (fail-soft): refill Volume=0 tip bars before API serves charts.
# Never blocks desk start; never promote; never change gates/min_conf.
try {
    Write-DeskLog 'tip_vol_heal: starting (Active+watchlist tip zeros)'
    $healOut = & $Python -c "from forex_lab.tip_vol_heal import heal_watchlist_tip_volumes, format_heal_log_line; s=heal_watchlist_tip_volumes(tip_bars=200, duka_lookback_hours=48, use_dukascopy=True, write=True, max_pairs=4); print(format_heal_log_line(s))" 2>&1
    foreach ($line in @($healOut)) { Write-DeskLog ([string]$line) }
} catch {
    Write-DeskLog ('tip_vol_heal_boot_skip: ' + $_.Exception.Message)
}

Write-DeskLog 'Starting uvicorn on 127.0.0.1:8000'
$apiArgs = @('-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000')
$apiProc = Start-Process -FilePath $Python -ArgumentList $apiArgs -WorkingDirectory $Root `
    -RedirectStandardOutput $ApiOut -RedirectStandardError $ApiErr -PassThru -WindowStyle Minimized

# Start Desk (npm.cmd on Windows)
Write-DeskLog 'Starting vite desk on 127.0.0.1:5173'
$npmPath = $npmCmd.Source
if ($npmPath -like '*.ps1') {
    # Prefer npm.cmd sibling to avoid execution-policy issues
    $npmCmdPath = Join-Path (Split-Path $npmPath) 'npm.cmd'
    if (Test-Path $npmCmdPath) { $npmPath = $npmCmdPath }
}
$deskProc = Start-Process -FilePath $npmPath -ArgumentList @('run', 'dev') -WorkingDirectory (Join-Path $Root 'desk') `
    -RedirectStandardOutput $DeskOut -RedirectStandardError $DeskErr -PassThru -WindowStyle Minimized

Write-DeskLog "API PID=$($apiProc.Id) Desk PID=$($deskProc.Id)"

$ok = $false
for ($i = 1; $i -le 45; $i++) {
    Start-Sleep -Seconds 1
    if ($apiProc.HasExited -and -not (Test-PortListening 8000)) {
        $errTail = ''
        if (Test-Path $ApiErr) { $errTail = (Get-Content $ApiErr -Raw -ErrorAction SilentlyContinue) }
        Write-DeskLog "ERROR: API process exited early code=$($apiProc.ExitCode) and :8000 not listening"
        Write-DeskLog "api_stderr: $errTail"
        Write-Host ''
        Write-Host 'API failed to start. See data\api_stderr.log'
        Write-Host $errTail
        try { if (-not $deskProc.HasExited) { Stop-Process -Id $deskProc.Id -Force } } catch {}
        Exit-DeskLock $lockPath
        exit 1
    } elseif ($apiProc.HasExited -and (Test-PortListening 8000)) {
        Write-DeskLog "API launcher PID exited but :8000 is listening (uvicorn child) - continuing"
    }
    if ($deskProc.HasExited -and -not (Test-PortListening 5173)) {
        $errTail = ''
        if (Test-Path $DeskErr) { $errTail = (Get-Content $DeskErr -Raw -ErrorAction SilentlyContinue) }
        Write-DeskLog "ERROR: Desk process exited early code=$($deskProc.ExitCode) and :5173 not listening"
        Write-DeskLog "desk_stderr: $errTail"
        Write-Host ''
        Write-Host 'Desk (vite) failed to start. See data\desk_stderr.log'
        Write-Host $errTail
        try { if (-not $apiProc.HasExited) { Stop-Process -Id $apiProc.Id -Force } } catch {}
        Stop-Port 5173
        Exit-DeskLock $lockPath
        exit 1
    } elseif ($deskProc.HasExited -and (Test-PortListening 5173)) {
        Write-DeskLog "Desk launcher PID exited but :5173 is listening (npm parent spawn) - continuing"
    }
    if (Test-Healthy) {
        $ok = $true
        Write-DeskLog "Healthy after ${i}s (API+$($apiProc.Id) Desk+$($deskProc.Id))"
        break
    }
    if (($i % 5) -eq 0) { Write-Host "Waiting for :8000/health and :5173 ... ($i/45)" }
}

if (-not $ok) {
    Write-DeskLog 'ERROR: timed out waiting for health'
    Exit-DeskLock $lockPath
    Write-Host ''
    Write-Host 'Timed out. Check data\api_stderr.log and data\desk_stderr.log'
    if (Test-Path $ApiErr) { Write-Host '--- api_stderr ---'; Get-Content $ApiErr -Tail 40 }
    if (Test-Path $DeskErr) { Write-Host '--- desk_stderr ---'; Get-Content $DeskErr -Tail 40 }
    exit 1
}

Write-Host ''
Write-Host 'ForX Decision desk is UP'
Write-Host '  API   http://127.0.0.1:8000/health'
Write-Host '  Desk  http://127.0.0.1:5173'
Write-Host "  Log   $LogFile"
Write-Host 'Stop with:  powershell -File START_DESK.ps1 -Stop'

if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:5173'
    Write-DeskLog 'Browser opened'
}

Write-DeskLog '===== START_DESK OK ====='
} finally {
    Exit-DeskLock $lockPath
}
exit 0
