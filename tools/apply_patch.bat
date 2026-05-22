@echo off
setlocal
python "%~dp0apply_patch.py" %*
exit /b %ERRORLEVEL%
