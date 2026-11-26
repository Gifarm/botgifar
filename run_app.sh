#!/bin/bash
# Jalankan aplikasi Flow Music Bot (GUI).
cd "$(dirname "$0")" || exit 1

if [ ! -x "./venv/bin/python" ]; then
    echo "venv belum ada. Membuat venv dan memasang dependensi..."
    python3 -m venv venv || exit 1
    ./venv/bin/pip install -r requirements.txt || exit 1
fi

exec ./venv/bin/python app.py
