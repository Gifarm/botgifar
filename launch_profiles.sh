#!/bin/bash
# Helper lama: buka N browser untuk login manual.
# Sekarang memakai main.py supaya flag browser sama dengan yang dipakai aplikasi
# (anti-throttle, mute audio, kontrol jendela via CDP).
#
# Usage: ./launch_profiles.sh [jumlah]

cd "$(dirname "$0")" || exit 1
N=${1:-5}

if [ ! -x "./venv/bin/python" ]; then
    echo "venv tidak ditemukan. Jalankan dulu:"
    echo "  python3 -m venv venv"
    echo "  ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

exec ./venv/bin/python main.py --login --browsers "$N"
