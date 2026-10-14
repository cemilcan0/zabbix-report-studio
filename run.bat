@echo off
chcp 65001 >nul
title Zabbix Report Studio

echo ==========================================
echo   Zabbix Report Studio - Starting
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

:: Install required packages (on first run)
echo [1/2] Checking dependencies...
pip install -r requirements.txt -q --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Package installation failed!
    pause
    exit /b 1
)

echo [2/2] Starting the application...
echo.
echo  Address : http://localhost:5050
echo  First sign-in : admin / Admin123!  (you will be asked to change it)
echo.
echo  Close this window to stop.
echo ==========================================

python app.py

pause
