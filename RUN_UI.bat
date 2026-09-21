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

python -c "import streamlit" 2>nul
if errorlevel 1 (
  echo Installing requirements including Streamlit...
  python -m pip install -U pip
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo pip install failed. Create a venv with INSTALL.bat, then retry RUN_UI.bat.
    pause
    exit /b 1
  )
)

echo.
echo Forex Research Lab UI - research only, no live orders
echo Browser: http://localhost:8501
echo Stop with Ctrl+C
echo.
python -m streamlit run streamlit_app.py --server.address localhost --server.port 8501
if errorlevel 1 pause
