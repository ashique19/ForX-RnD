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
    echo pip install failed.
    pause
    exit /b 1
  )
)

echo.
echo ForX Decision API - research only, no live orders
echo http://127.0.0.1:8000/health
echo Streamlit Lab is unchanged: RUN_UI.bat on port 8501
echo Stop with Ctrl+C
echo.
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause
