@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "MC_ROOT=%CD%"
set "MC_START_STOP=%MC_ROOT%\scripts\main-computer-start-stop.ps1"

if not defined MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS set "MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS=20"

if not exist "%MC_START_STOP%" (
  echo Missing startup helper:
  echo   %MC_START_STOP%
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action dev-hub-start -Root "%MC_ROOT%" -StartedBy "dev-hub-start.bat"
if errorlevel 1 (
  echo Failed to start the Main Computer dev Hub.
  exit /b %ERRORLEVEL%
)

exit /b 0
