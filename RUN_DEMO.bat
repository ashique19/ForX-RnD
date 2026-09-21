@echo off
cd /d %~dp0
call .venv\Scripts\activate.bat
python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
if errorlevel 1 python -m forex_lab fetch --pair EURUSD --synthetic
python -m forex_lab train --pair EURUSD
python -m forex_lab backtest --pair EURUSD
python -m forex_lab signals --pair EURUSD
echo.
echo See reports\latest_report.md and signals\latest_signals.csv
pause
