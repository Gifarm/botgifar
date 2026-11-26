@echo off
REM Jalankan aplikasi Flow Music Bot (GUI) di Windows.
cd /d "%~dp0"

if not exist ".\venv\Scripts\python.exe" (
    echo venv belum ada. Membuat venv dan memasang dependensi...
    python -m venv venv || goto :error
    ".\venv\Scripts\pip.exe" install -r requirements.txt || goto :error
)

".\venv\Scripts\python.exe" app.py
goto :eof

:error
echo Gagal menyiapkan venv.
pause
