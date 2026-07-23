@echo off
setlocal EnableExtensions
title Build Manuscript SQLite
set PYTHONUTF8=1

pushd "%~dp0" || (
  echo Cannot enter script directory: %~dp0
  echo.
  pause
  exit /b 1
)

echo Building manuscript SQLite database...
echo Working directory: %CD%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Please install Python or add it to PATH.
  echo.
  pause
  exit /b 1
)

python ".\scripts\manuscript_sqlite_etl.py" --build
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo Build failed. Exit code: %EXIT_CODE%
  echo.
  pause
  exit /b %EXIT_CODE%
)

echo SQLite build finished.
echo Output database updated successfully.
echo.
pause
