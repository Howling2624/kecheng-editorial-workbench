@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
python tools\private_module.py export --project-root "%~dp0"
echo.
pause
