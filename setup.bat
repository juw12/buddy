@echo off
rem One-time setup: creates .venv and installs Buddy's dependencies.
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv || goto :fail
)
.venv\Scripts\python.exe -m pip install --upgrade pip || goto :fail
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
.venv\Scripts\python.exe buddy.py --selftest || goto :fail
echo.
echo Setup OK. Run start.bat to launch Buddy.
pause
exit /b 0
:fail
echo Setup failed.
pause
exit /b 1
