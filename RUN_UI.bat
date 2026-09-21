@echo off
cd /d %~dp0
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
) else (
  echo No .venv found - using current Python. Run INSTALL.bat first if imports fail.
)

python -c "import fastapi,uvicorn" 2>nul
if errorlevel 1 (
  echo Installing requirements including FastAPI...
  python -m pip install -U pip
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo pip install failed. Create a venv with INSTALL.bat, then retry RUN_UI.bat.
    pause
    exit /b 1
  )
)

if not exist desk\node_modules (
  echo Installing desk packages...
  pushd desk
  call npm install
  if errorlevel 1 (
    echo npm install failed.
    popd
    pause
    exit /b 1
  )
  popd
)

echo.
echo ForX Decision desk
echo   API   http://127.0.0.1:8000
echo   Desk  http://127.0.0.1:5173
echo Stop by closing the "ForX API" and "ForX Desk" windows.
echo Streamlit lab stays on RUN_LAB.bat  http://localhost:8501
echo.

start "ForX API" cmd /k "cd /d %~dp0 && if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat && python -m uvicorn api.main:app --host 127.0.0.1 --port 8000"
start "ForX Desk" cmd /k "cd /d %~dp0desk && npm run dev"
start "" http://127.0.0.1:5173
