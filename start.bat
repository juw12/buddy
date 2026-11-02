@echo off
rem Launch Buddy without a console window.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
start "" .venv\Scripts\pythonw.exe buddy.py
