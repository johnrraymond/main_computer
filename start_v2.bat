@echo off
setlocal EnableExtensions

rem Experimental v2 launcher. It force-stops current app processes before launching.
rem It is intentionally location-aware:
rem   * source/dev checkout: uses python from PATH and source defaults
rem   * installed tree: uses runtime\start_stop\main-computer-launcher.json
rem     when the Python installer prepared one

cd /d "%~dp0"
set "MC_ROOT=%CD%"
set "MC_START_STOP=%MC_ROOT%\scripts\main-computer-start-stop.ps1"

rem The applications/Coolify core stack must have exactly one Docker Compose
rem project name. Do not let an old shell-level Coolify publish project leak
rem into start_v2 and create a second stack.
set "MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT=main-computer-applications"
set "MAIN_COMPUTER_COOLIFY_PROJECT="
set "COOLIFY_COMPOSE_PROJECT="
set "COMPOSE_PROJECT_NAME="

if not exist "%MC_START_STOP%" (
  echo Missing startup helper:
  echo   %MC_START_STOP%
  exit /b 1
)

echo Force-stopping current Main Computer app processes before launch; Docker stacks are left alone...
powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action start -Root "%MC_ROOT%" -StartedBy "start_v2.bat"
if errorlevel 1 (
  echo Failed to launch Main Computer app control/supervisor.
  exit /b %ERRORLEVEL%
)

echo Waiting briefly for startup status...
powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action status -Root "%MC_ROOT%" -StartedBy "start_v2.bat"
if errorlevel 1 (
  echo Startup was requested, but status did not report cleanly.
  exit /b %ERRORLEVEL%
)

echo.
echo Refresh status by running .\start_v2.bat again.
echo Stop everything started by this script with:
echo   .\stop_v2.bat
echo Detailed supervisor JSON is available through the resolved Python:
echo   powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action status -Root "%MC_ROOT%"
echo.

exit /b 0
