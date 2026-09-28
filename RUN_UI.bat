@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist "data" mkdir "data"
set "LOG=data\desk_start.log"
set "API_OUT=data\api_stdout.log"
set "DESK_OUT=data\desk_stdout.log"

call :log "===== RUN_UI start ====="
call :log "cwd=%CD%"

REM --- free stale listeners on 8000 / 5173 (common after unclean shutdown) ---
call :free_port 8000
call :free_port 5173

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
  call :log "activated .venv"
) else (
  call :log "WARNING: no .venv - using PATH python"
  echo No .venv found - using current Python. Run INSTALL.bat first if imports fail.
)

where python >nul 2>&1
if errorlevel 1 (
  call :log "ERROR: python not on PATH"
  echo Python not found. Run INSTALL.bat first.
  pause
  exit /b 1
)

python -c "import fastapi,uvicorn" 2>nul
if errorlevel 1 (
  echo Installing requirements including FastAPI...
  call :log "pip install requirements (fastapi/uvicorn missing)"
  python -m pip install -U pip
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    call :log "ERROR: pip install failed"
    echo pip install failed. Create a venv with INSTALL.bat, then retry RUN_UI.bat.
    pause
    exit /b 1
  )
)

where npm >nul 2>&1
if errorlevel 1 (
  call :log "ERROR: npm missing"
  echo Node.js npm is MISSING. Install Node 20+ from https://nodejs.org/
  pause
  exit /b 1
)

if not exist "desk\node_modules" (
  echo Installing desk packages...
  call :log "npm install (no node_modules)"
  pushd desk
  call npm install
  if errorlevel 1 (
    call :log "ERROR: npm install failed"
    popd
    pause
    exit /b 1
  )
  popd
)

REM Clear Vite prebundle every cold start (CandlestickSeries missing export after boot)
if exist "desk\node_modules\.vite" (
  call :log "clearing desk\node_modules\.vite"
  rmdir /s /q "desk\node_modules\.vite" 2>nul
)

REM clear prior run logs
type nul > "%API_OUT%"
type nul > "%DESK_OUT%"

echo.
echo ForX Decision desk
echo   API   http://127.0.0.1:8000
echo   Desk  http://127.0.0.1:5173
echo   Logs  %CD%\%LOG%
echo Stop by closing the "ForX API" and "ForX Desk" windows, or run START_DESK.ps1 -Stop
echo.

REM Prefer PowerShell launcher when available (more reliable than nested cmd /k)
where powershell >nul 2>&1
if not errorlevel 1 (
  if exist "%~dp0START_DESK.ps1" (
    call :log "delegating to START_DESK.ps1"
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_DESK.ps1" -FromBat
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
      call :log "START_DESK.ps1 failed rc=!RC!"
      echo.
      echo Start failed. See %LOG% and data\api_stdout.log / data\desk_stdout.log
      pause
      exit /b !RC!
    )
    exit /b 0
  )
)

REM Fallback: classic two cmd windows + health wait
call :log "fallback: start cmd /k windows"
start "ForX API" cmd /k "cd /d "%~dp0" && if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat && python -m uvicorn api.main:app --host 127.0.0.1 --port 8000"
start "ForX Desk" cmd /k "cd /d "%~dp0desk" && npm run dev"

call :wait_health
if errorlevel 1 (
  call :log "ERROR: health wait failed (fallback path)"
  echo Servers did not become healthy. Check the ForX API / ForX Desk windows and %LOG%
  pause
  exit /b 1
)

start "" http://127.0.0.1:5173
call :log "browser opened (fallback path OK)"
exit /b 0

:free_port
set "PORT=%~1"
set "KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  if not "%%P"=="0" (
    call :log "killing PID %%P holding port %PORT%"
    taskkill /F /PID %%P >nul 2>&1
    set "KILLED=1"
  )
)
if "!KILLED!"=="1" (
  timeout /t 1 /nobreak >nul
)
exit /b 0

:wait_health
set /a "TRIES=0"
:wait_loop
set /a "TRIES+=1"
powershell -NoProfile -Command "try { $h=(Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 2).Content; $u=(Invoke-WebRequest -Uri http://127.0.0.1:5173/ -UseBasicParsing -TimeoutSec 2).StatusCode; if ($h -match 'ok' -and $u -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 (
  call :log "healthy after !TRIES! tries"
  echo API and Desk are up.
  exit /b 0
)
if !TRIES! GEQ 40 (
  call :log "gave up after !TRIES! tries"
  exit /b 1
)
echo Waiting for API :8000 and Desk :5173 ... (!TRIES!/40)
timeout /t 1 /nobreak >nul
goto wait_loop

:log
echo [%DATE% %TIME%] %~1>> "%LOG%"
exit /b 0

