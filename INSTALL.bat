@echo off
setlocal
cd /d %~dp0
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
echo Using: %PY%
"%PY%" -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -U pip
pip install -r requirements.txt
echo.
echo Install done. Next:
echo   RUN_UI.bat
echo or:
echo   python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
echo   python -m forex_lab train --pair EURUSD
echo   python -m forex_lab backtest --pair EURUSD
echo   python -m forex_lab signals --pair EURUSD
endlocal
