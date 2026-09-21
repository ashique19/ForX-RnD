@echo off
cd /d %~dp0
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
call .venv\Scripts\activate.bat
python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
if errorlevel 1 (
  if exist data\EURUSD_1h.csv (
    echo Fetch reported an error but data\EURUSD_1h.csv exists - not overwriting with synthetic.
  ) else (
    python -m forex_lab fetch --pair EURUSD --synthetic
  )
)
python -m forex_lab train --pair EURUSD
python -m forex_lab backtest --pair EURUSD
python -m forex_lab signals --pair EURUSD
echo.
echo See reports\latest_report.md and signals\latest_signals.csv
pause
