@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
set "STOP_FLAG=%~dp0.local\editops.stop-requested"
del /q "%STOP_FLAG%" >nul 2>nul
set "PYTHON_EXE="
for /f "usebackq delims=" %%I in (`python -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE (
  echo 未找到可用的 Python，请先安装 Python。
  pause
  exit /b 1
)
"%PYTHON_EXE%" app.py
set "EXIT_CODE=%ERRORLEVEL%"
if exist "%STOP_FLAG%" (
  del /q "%STOP_FLAG%" >nul 2>nul
  exit /b 0
)
if not "%EXIT_CODE%"=="0" (
  echo.
  echo 工作台启动失败，请先运行：pip install -r requirements.txt
  pause
)
exit /b %EXIT_CODE%
