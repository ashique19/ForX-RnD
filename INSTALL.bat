@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM Quiet: INSTALL_QUIET=1 or --quiet (no pause / no launch prompt)
REM Launch UI after success: INSTALL_LAUNCH_UI=1 or --launch-ui
set "LAUNCH_UI=%INSTALL_LAUNCH_UI%"
set "QUIET=%INSTALL_QUIET%"
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--launch-ui" set "LAUNCH_UI=1"
if /I "%~1"=="/launch-ui" set "LAUNCH_UI=1"
if /I "%~1"=="--quiet" set "QUIET=1"
if /I "%~1"=="/quiet" set "QUIET=1"
shift
goto parse_args
:args_done

echo.
echo ============================================
echo  Forex Research Lab - Windows installer
echo ============================================
echo  Research only. No live broker. No live orders.
echo.

set "PY="
call :find_python
if not defined PY (
  call :print_missing_python
  call :maybe_pause
  exit /b 1
)

echo Using Python: %PY%
echo.

"%PY%" -m forex_lab.install_check --install --python "%PY%" --root "%CD%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Install stopped. The checklist above lists MISSING items.
  call :maybe_pause
  exit /b %RC%
)

echo.
echo Next:
echo   RUN_UI.bat
echo or:
echo   python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
echo.

if /I "%LAUNCH_UI%"=="1" goto launch_ui
if /I "%QUIET%"=="1" goto done

set "ANSWER="
set /p ANSWER=Launch the trader desk now (RUN_UI.bat)? [Y/N] 
if /I "!ANSWER!"=="Y" goto launch_ui
if /I "!ANSWER!"=="YES" goto launch_ui
goto done

:launch_ui
echo.
call "%~dp0RUN_UI.bat"
set "RC=%ERRORLEVEL%"
exit /b %RC%

:done
endlocal
exit /b 0

REM ------------------------------------------------------------------
:maybe_pause
if /I "%QUIET%"=="1" exit /b 0
pause
exit /b 0

REM ------------------------------------------------------------------
:print_missing_python
echo Preflight checklist
echo Root: %CD%
echo.
echo   [MISSING] Python 3.11+     not found on PATH
echo             -^> Install Python 3.11 or newer from
echo                https://www.python.org/downloads/
echo                On Windows tick "Add python.exe to PATH" and leave pip
echo                checked, then re-run INSTALL.bat.
echo   [MISSING] pip              needs Python
echo   [MISSING] venv module      needs Python
call :check_write_bat
call :check_requirements_bat
echo   [MISSING] Network (pip)    not checked (optional; needs Python to probe PyPI)
echo.
echo Result: required item(s) MISSING (Python 3.11+).
echo This installer cannot continue without Python.
echo.
exit /b 0

:check_write_bat
(echo ok > "%CD%\.install_write_probe") 2>nul
if exist "%CD%\.install_write_probe" (
  del "%CD%\.install_write_probe" >nul 2>&1
  echo   [OK]      Write access     can write %CD%
) else (
  echo   [MISSING] Write access     cannot write %CD%
  echo             -^> Copy the project to a writable folder and re-run.
)
exit /b 0

:check_requirements_bat
if exist "%CD%\requirements.txt" (
  echo   [OK]      requirements.txt found requirements.txt
) else (
  echo   [MISSING] requirements.txt not found
  echo             -^> Run INSTALL.bat from the Forex Research Lab folder.
)
exit /b 0

REM ------------------------------------------------------------------
:find_python
if defined FORX_PYTHON (
  if exist "%FORX_PYTHON%" (
    set "PY=%FORX_PYTHON%"
    exit /b 0
  )
)

where py >nul 2>&1
if not errorlevel 1 (
  for %%V in (3.14 3.13 3.12 3.11 3) do (
    if "!PY!"=="" (
      for /f "delims=" %%i in ('py -%%V -c "import sys; print(sys.executable)" 2^>nul') do (
        if exist "%%i" if "!PY!"=="" set "PY=%%i"
      )
    )
  )
)

if not "!PY!"=="" exit /b 0

for %%C in (python3.14 python3.13 python3.12 python3.11 python3 python) do (
  if "!PY!"=="" (
    where %%C >nul 2>&1
    if not errorlevel 1 (
      for /f "delims=" %%i in ('%%C -c "import sys; print(sys.executable)" 2^>nul') do (
        if exist "%%i" if "!PY!"=="" set "PY=%%i"
      )
    )
  )
)

if not "!PY!"=="" exit /b 0

for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%ProgramFiles%\Python314\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
) do (
  if "!PY!"=="" (
    if exist %%P set "PY=%%~P"
  )
)

if defined PY exit /b 0
exit /b 1
