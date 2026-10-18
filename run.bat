@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] Python tidak ditemukan di PATH.
  echo     Install dari https://www.python.org/downloads/ lalu jalankan lagi.
  pause
  exit /b 1
)

python -m mirrorflow %*

if errorlevel 1 pause
endlocal
