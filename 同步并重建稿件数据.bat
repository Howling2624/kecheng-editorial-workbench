@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

echo 正在从 Google Drive 下载最新稿件信息表...
python "modules\data_summary\稿件表数据\数据同步工具\backup_sheets.py"
if errorlevel 1 (
  echo.
  echo 下载失败，请确认已经导入私有配置模块并完成 Google 授权。
  pause
  exit /b 1
)

echo.
echo 正在将 Excel 转换为 SQLite...
python "modules\data_summary\scripts\manuscript_sqlite_etl.py" --build
if errorlevel 1 (
  echo.
  echo SQLite 构建失败，请检查映射配置和下载的 Excel。
  pause
  exit /b 1
)

echo.
echo 数据同步和建库已完成。
pause
