@echo off
cd /d %~dp0
chcp 65001 >nul

where npm >nul 2>&1
if errorlevel 1 (
  echo Node.js npm is MISSING. Install Node 20+ from https://nodejs.org/ then re-run RUN_DESK.bat.
  pause
  exit /b 1
)

cd desk
if not exist node_modules (
  echo Installing desk dependencies...
  call npm install
  if errorlevel 1 (
    echo npm install failed.
    pause
    exit /b 1
  )
)

echo.
echo ForX Decision desk
echo http://127.0.0.1:5173
echo Start the API first: RUN_API.bat  (port 8000)
echo Streamlit Lab stays on port 8501 until cutover.
echo Stop with Ctrl+C
echo.
call npm run dev
if errorlevel 1 pause
