@echo off
setlocal EnableExtensions

cd /d "%~dp0"

call "%~dp0stop_v2.bat" %*
exit /b %ERRORLEVEL%