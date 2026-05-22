@echo off
setlocal EnableExtensions

rem Stop Main Computer from this source tree. This is the counterpart to
rem start.bat: it reads runtime\start_stop\start-session.json when present
rem and falls back to the supervisor/PID state files that start.bat creates.

cd /d "%~dp0"
set "MC_ROOT=%CD%"
set "MC_START_STOP=%MC_ROOT%\scripts\main-computer-start-stop.ps1"

if not exist "%MC_START_STOP%" (
  echo Missing stop helper:
  echo   %MC_START_STOP%
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action stop -Root "%MC_ROOT%"
if errorlevel 1 (
  echo Failed to stop Main Computer cleanly.
  exit /b %ERRORLEVEL%
)

echo.
echo Stop reports are written under:
echo   %MC_ROOT%\runtime\start_stop
echo.

exit /b 0
