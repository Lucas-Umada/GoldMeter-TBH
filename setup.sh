#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v tesseract >/dev/null || ! python3 -c 'import tkinter' 2>/dev/null; then
    echo ">> Missing system packages. Run:"
    echo "   sudo pacman -S --needed tesseract tesseract-data-eng tk"
fi

python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
echo ">> Python deps installed."
echo ">> Next: start the game, then:"
echo "   .venv/bin/python -m goldmeter calibrate"
echo "   .venv/bin/python -m goldmeter"
