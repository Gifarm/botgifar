#!/bin/bash
# Script untuk buka N profile Brave/Chromium (default 5)
# Usage: ./launch_profiles.sh [jumlah]

N=${1:-5}

BRAVE="/usr/bin/brave-browser"  # sesuaikan: chromium-browser, google-chrome, dll
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Membuka $N profile browser..."

for i in $(seq 1 $N); do
    PORT=$((9221 + i))
    PROFILE_DIR="$BASE_DIR/profiles/profile_$i"
    mkdir -p "$PROFILE_DIR"
    echo "  → Profile $i (port $PORT)"
    $BRAVE --remote-debugging-port=$PORT --user-data-dir="$PROFILE_DIR" &
    sleep 2
done

echo "✅ $N profile dibuka. Login manual, lalu jalankan bot."