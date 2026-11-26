@echo off
REM Helper lama: buka N browser untuk login manual.
REM Sekarang memakai main.py supaya flag browser sama dengan aplikasi.
REM Usage: launch_profiles.bat [jumlah]

setlocal
cd /d "%~dp0"

set N=%1
if "%N%"=="" set N=5

if not exist ".\venv\Scripts\python.exe" (
    echo venv tidak ditemukan. Jalankan dulu:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

".\venv\Scripts\python.exe" main.py --login --browsers %N%
pause
