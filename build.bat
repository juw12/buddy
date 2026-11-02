@echo off
rem Build Buddy into dist\Buddy\ (folder app) and dist\Buddy-win64.zip.
rem Your Pips data in dist\Buddy\app\pips (config.json, data\) survives rebuilds and is never zipped.
setlocal
cd /d "%~dp0"
if "%PYTHON%"=="" set "PYTHON=.venv\Scripts\python.exe"
set "KEEP=build\keep-profile"

rem A running Buddy holds your data open: ask it to quit first, and stop if it will not
tasklist /fi "imagename eq Buddy.exe" | find /i "Buddy.exe" >nul && (
    echo Asking the running Buddy to quit...
    if exist dist\Buddy\Buddy.exe start "" /wait dist\Buddy\Buddy.exe --quit
    ping -n 7 127.0.0.1 >nul
)
tasklist /fi "imagename eq Buddy.exe" | find /i "Buddy.exe" >nul && (
    echo Buddy is still running - quit it from the tray, then run build.bat again.
    exit /b 1
)

if exist "%KEEP%" rmdir /s /q "%KEEP%"
mkdir "%KEEP%"
if exist dist\Buddy\app\pips\config.json move dist\Buddy\app\pips\config.json "%KEEP%\" >nul
if exist dist\Buddy\app\pips\data move dist\Buddy\app\pips\data "%KEEP%\data" >nul
if exist dist\Buddy\app\pips\data (
    echo Could not move your Buddy data aside - nothing was changed.
    if exist "%KEEP%\config.json" move "%KEEP%\config.json" dist\Buddy\app\pips\ >nul
    exit /b 1
)

"%PYTHON%" -m PyInstaller --noconfirm --clean buddy.spec || goto :fail

if not exist dist\Buddy\app mkdir dist\Buddy\app
copy /y buddy_host.py dist\Buddy\app\ >nul
copy /y pips_import.py dist\Buddy\app\ >nul
copy /y buddy_home.py dist\Buddy\app\ >nul
robocopy assets dist\Buddy\app\assets /e /njh /njs /nfl /ndl >nul
if errorlevel 8 goto :fail
robocopy pips dist\Buddy\app\pips /e /njh /njs /nfl /ndl /xd data logs output backup .release __pycache__ /xf config.json >nul
if errorlevel 8 goto :fail
robocopy keyit dist\Buddy\app\keyit /e /njh /njs /nfl /ndl /xd __pycache__ tests >nul
if errorlevel 8 goto :fail

powershell -NoProfile -Command "Compress-Archive -Path dist\Buddy\* -DestinationPath dist\Buddy-win64.zip -Force" || goto :fail

if exist "%KEEP%\config.json" move "%KEEP%\config.json" dist\Buddy\app\pips\ >nul
if exist "%KEEP%\data" move "%KEEP%\data" dist\Buddy\app\pips\data >nul
echo.
echo Built dist\Buddy\Buddy.exe and dist\Buddy-win64.zip
exit /b 0

:fail
if exist "%KEEP%\data" if not exist dist\Buddy\app\pips\data (
    echo Build failed - your Pips profile was kept in %KEEP%
)
exit /b 1
