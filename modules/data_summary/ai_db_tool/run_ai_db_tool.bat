@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set "AI_DB_ROOT=%~dp0.."

if "%DEEPSEEK_MODEL%"=="" set DEEPSEEK_MODEL=deepseek-chat
if "%DEEPSEEK_API_URL%"=="" set DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions

if "%DEEPSEEK_API_KEY%"=="" if not exist "%~dp0config.json" (
  echo.
  echo 未检测到 DEEPSEEK_API_KEY。
  echo 可以临时粘贴 API Key；窗口关闭后不会保存。
  echo 也可以启动后在网页右上角“配置 API”中保存到本机 config.json。
  set /p DEEPSEEK_API_KEY=请输入 DeepSeek API Key:
)

python ".\app.py" --host 127.0.0.1 --port 8765
pause
