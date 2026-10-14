@echo off
chcp 65001 >nul
title Zabbix Report Studio

echo ==========================================
echo   Zabbix Report Studio - Desktop App
echo ==========================================
echo.

:: Check that Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo Install Python 3.10+: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install required packages (including pywebview, on first run)
echo [1/2] Checking dependencies...
pip install -r requirements.txt -q --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Package installation failed!
    pause
    exit /b 1
)

echo [2/2] Launching the application...
:: pythonw = no console; the app opens in its own window and this window closes
start "" pythonw "%~dp0desktop.py"

exit /b 0
