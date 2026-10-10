@echo off
REM Buka N profile Brave/Chromium (default 5)
REM Usage: launch_profiles.bat [jumlah]

setlocal enabledelayedexpansion

set N=%1
if "%N%"=="" set N=5

REM SESUAIKAN PATH BRAVE DI SINI
set BRAVE="C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
set BASE_DIR=%~dp0

echo Membuka %N% profile browser...

for /L %%i in (1,1,%N%) do (
    set /a PORT=9221+%%i
    set PROFILE_DIR=%BASE_DIR%profiles\profile_%%i
    if not exist "!PROFILE_DIR!" mkdir "!PROFILE_DIR!"
    echo   - Profile %%i ^(port !PORT!^)
    start "" %BRAVE% --remote-debugging-port=!PORT! --user-data-dir="!PROFILE_DIR!"
    timeout /t 2 /nobreak > nul
)

echo.
echo ✅ %N% profile dibuka. Login manual, lalu jalankan bot.
pause