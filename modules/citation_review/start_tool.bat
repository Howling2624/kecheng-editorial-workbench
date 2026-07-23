@echo off
setlocal
cd /d "%~dp0"

python -c "import flask, requests" >nul 2>nul
if errorlevel 1 (
    echo Missing Python dependencies. Installing from requirements.txt...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Dependency installation failed. Please check the error above.
        pause
        exit /b 1
    )
)

echo Starting Citation Checker...
echo The browser will open automatically after the server is ready.
echo Keep this window open while using the tool.
echo.
set APP_OPEN_BROWSER=1
python app.py

echo.
echo The server has stopped.
pause
