@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
if "%~1"=="" (
  python tools\private_module.py import --project-root "%~dp0"
) else (
  python tools\private_module.py import "%~f1" --project-root "%~dp0"
)
echo.
pause
