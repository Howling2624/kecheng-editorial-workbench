@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "PID_FILE=%~dp0.local\editops.pid"
set "STOP_FLAG=%~dp0.local\editops.stop-requested"
set "EDITOPS_PID="
if exist "%PID_FILE%" (
  set /p "EDITOPS_PID="<"%PID_FILE%"
) else (
  for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr "127.0.0.1:8088" ^| findstr "LISTENING"') do set "EDITOPS_PID=%%P"
  if defined EDITOPS_PID echo 已发现旧版残留的 EditOps 进程。
)

if not defined EDITOPS_PID (
  echo 未发现正在运行的 EditOps。
  pause
  exit /b 0
)

echo(%EDITOPS_PID%| findstr /R "^[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo PID 文件无效，未执行任何结束操作。
  pause
  exit /b 1
)

wmic process where "processid=%EDITOPS_PID%" get CommandLine /value 2>nul | findstr /I "app.py" >nul
if errorlevel 1 (
  echo 未找到对应的 EditOps 主进程，正在清理过期 PID 文件。
  del /q "%PID_FILE%" >nul 2>nul
  pause
  exit /b 0
)

echo 正在停止 EditOps 及其三个业务模块...
> "%STOP_FLAG%" echo stop
taskkill /PID %EDITOPS_PID% /T /F >nul 2>nul
if errorlevel 1 (
  del /q "%STOP_FLAG%" >nul 2>nul
  echo 停止失败，请在任务管理器中结束 PID %EDITOPS_PID%。
  pause
  exit /b 1
)

del /q "%PID_FILE%" >nul 2>nul
echo EditOps 已完全停止。
pause
