@echo off
setlocal EnableExtensions

rem Experimental v2 stopper. It force-stops the currently running Main Computer
rem app processes, including stale processes that are no longer represented by
rem runtime PID/session files. Docker stacks are left alone by default.
rem
rem Use:
rem   stop_v2.bat --with-docker
rem only when you intentionally want to tear down tracked Docker infrastructure too.

cd /d "%~dp0"
set "MC_ROOT=%CD%"
set "MC_START_STOP=%MC_ROOT%\scripts\main-computer-start-stop.ps1"
set "MC_DOCKER_FLAG=-NoDocker"

if /I "%~1"=="--no-docker" set "MC_DOCKER_FLAG=-NoDocker"
if /I "%~1"=="/no-docker" set "MC_DOCKER_FLAG=-NoDocker"
if /I "%~1"=="--with-docker" set "MC_DOCKER_FLAG="
if /I "%~1"=="/with-docker" set "MC_DOCKER_FLAG="
if /I "%~1"=="--docker" set "MC_DOCKER_FLAG="

if not exist "%MC_START_STOP%" (
  echo Missing stop helper:
  echo   %MC_START_STOP%
  exit /b 1
)

if "%MC_DOCKER_FLAG%"=="-NoDocker" (
  echo Force-stopping current Main Computer app processes; Docker stacks are left alone...
) else (
  echo Force-stopping current Main Computer app processes and tracked Docker stacks...
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action stop -Root "%MC_ROOT%" -StartedBy "stop_v2.bat" %MC_DOCKER_FLAG%
if errorlevel 1 (
  echo Failed to stop Main Computer cleanly.
  exit /b %ERRORLEVEL%
)

echo.
echo Stop reports are written under:
echo   %MC_ROOT%\runtime\start_stop
echo.
if "%MC_DOCKER_FLAG%"=="-NoDocker" (
  echo Docker infrastructure was left running by default.
  echo Use .\stop_v2.bat --with-docker only when you intentionally want to tear it down too.
  echo.
)

exit /b 0
