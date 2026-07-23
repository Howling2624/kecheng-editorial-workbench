@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python app.py
if errorlevel 1 (
  echo.
  echo 工作台启动失败，请先运行：pip install -r requirements.txt
  pause
)
