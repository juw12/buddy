@echo off

rem Start Pips.bat - one-click start for Pips (desktop pet + Pomodoro widget).

rem First run: installs the two helper libraries (pywin32, Pillow) for this user.

title Pips

cd /d "%~dp0"
if exist "%~dp0runtime\pythonw.exe" (
    start "" "%~dp0runtime\pythonw.exe" "%~dp0app.py"
    exit /b 0
)



set "PY="

where py >nul 2>nul && set "PY=py -3"

if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY goto :nopython



%PY% -c "import tkinter" >nul 2>nul || goto :notk

%PY% -c "import win32gui, PIL" >nul 2>nul

if errorlevel 1 (

    echo Setting up Pips for the first time ^(installing pywin32 + Pillow^)...

    %PY% -m pip install --user --quiet --disable-pip-version-check pywin32 Pillow

)



where pyw >nul 2>nul && (start "" pyw -3 app.py & exit /b 0)

where pythonw >nul 2>nul && (start "" pythonw app.py & exit /b 0)

start "" %PY% app.py

exit /b 0



:nopython

echo.

echo  Pips needs Python 3 (free). Opening the download page...

echo  Install it, tick "Add python.exe to PATH", then double-click "Start Pips.bat" again.

start "" https://www.python.org/downloads/windows/

pause

exit /b 1



:notk

echo.

echo  Your Python is missing tkinter. Re-install Python from python.org with the default options.

pause

exit /b 1

