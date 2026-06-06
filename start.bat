@echo off
setlocal EnableExtensions

cd /d "%~dp0"

call "%~dp0start_v2.bat" %*
exit /b %ERRORLEVEL%