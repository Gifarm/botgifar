#!/bin/bash
# Script untuk buka 5 profile Brave/Chromium sekaligus (Linux)

BRAVE="/usr/bin/brave-browser"  # atau "chromium-browser" atau "google-chrome"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Membuka 5 profile browser..."

$BRAVE --remote-debugging-port=9222 --user-data-dir="$BASE_DIR/profiles/profile_1" &
sleep 2
$BRAVE --remote-debugging-port=9223 --user-data-dir="$BASE_DIR/profiles/profile_2" &
sleep 2
$BRAVE --remote-debugging-port=9224 --user-data-dir="$BASE_DIR/profiles/profile_3" &
sleep 2
$BRAVE --remote-debugging-port=9225 --user-data-dir="$BASE_DIR/profiles/profile_4" &
sleep 2
$BRAVE --remote-debugging-port=9226 --user-data-dir="$BASE_DIR/profiles/profile_5" &

echo "✅ 5 profile dibuka. Login manual, lalu jalankan: python main.py"
