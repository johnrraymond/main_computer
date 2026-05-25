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

set "MC_OPEN_BROWSER=0"
set "MC_UNKNOWN_ARG="

:mc_parse_args
if "%~1"=="" goto mc_args_done
if /I "%~1"=="-OpenBrowser" goto mc_enable_open_browser
if /I "%~1"=="--open-browser" goto mc_enable_open_browser
if /I "%~1"=="/OpenBrowser" goto mc_enable_open_browser
set "MC_UNKNOWN_ARG=%~1"
goto mc_args_done

:mc_enable_open_browser
set "MC_OPEN_BROWSER=1"
shift
goto mc_parse_args

:mc_args_done
if defined MC_UNKNOWN_ARG (
  echo Unknown argument: %MC_UNKNOWN_ARG%
  echo Usage: start_v2.bat [-OpenBrowser]
  exit /b 2
)

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

if not "%MC_OPEN_BROWSER%"=="1" goto mc_skip_open_browser

set "MC_OPEN_BROWSER_HELPER=%MC_ROOT%\scripts\open-main-computer-browser.ps1"
if not exist "%MC_OPEN_BROWSER_HELPER%" (
  echo Missing browser helper:
  echo   %MC_OPEN_BROWSER_HELPER%
  exit /b 1
)

echo.
echo Waiting for Main Computer to answer before opening the browser...
powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_OPEN_BROWSER_HELPER%" -Root "%MC_ROOT%" -TimeoutSeconds 120
if errorlevel 1 (
  echo Main Computer started, but browser open/wait failed.
  exit /b %ERRORLEVEL%
)

:mc_skip_open_browser

echo.
echo Refresh status by running .\start_v2.bat again.
echo Stop everything started by this script with:
echo   .\stop_v2.bat
echo Detailed supervisor JSON is available through the resolved Python:
echo   powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action status -Root "%MC_ROOT%"
echo.

exit /b 0
