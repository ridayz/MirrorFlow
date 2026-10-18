@echo off
setlocal
cd /d "%~dp0"
python -m mirrorflow --demo %*
if errorlevel 1 pause
endlocal
