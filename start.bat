@echo off
setlocal EnableExtensions

rem Start Main Computer from this source tree. This script is intentionally the
rem owner of the local startup flow; stop.bat reads the runtime session that this
rem script writes so it can stop the same Python services and Docker stacks.

cd /d "%~dp0"
set "MC_ROOT=%CD%"
set "MC_START_STOP=%MC_ROOT%\scripts\main-computer-start-stop.ps1"

if not exist "%MC_START_STOP%" (
  echo Missing startup helper:
  echo   %MC_START_STOP%
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action start -Root "%MC_ROOT%"
if errorlevel 1 (
  echo Failed to launch Main Computer app control/supervisor.
  exit /b %ERRORLEVEL%
)

echo Waiting briefly for startup status...
python -m main_computer.service_supervisor --root "%MC_ROOT%" status --summary --wait-s 30 --interval-s 2

echo.
echo Refresh again by running .\start.bat again.
echo Stop everything started by this script with:
echo   .\stop.bat
echo Detailed supervisor JSON:
echo   python -m main_computer.service_supervisor --root "%MC_ROOT%" status
echo.

exit /b 0
