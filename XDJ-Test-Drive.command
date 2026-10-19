#!/bin/bash
# XDJ-700 Test Drive -- double-click me. Sets everything up, opens Studio.
cd "$(dirname "$0")"
echo "=== XDJ-700 Test Drive setup (one-time, ~5 min) ==="
if [ ! -d .testvenv ]; then
  python3 -m venv .testvenv || { echo "Need Python 3.12+ from python.org"; exit 1; }
fi
.testvenv/bin/pip install -q numpy Pillow pygame hidapi 2>&1 | tail -n 1
echo "=== Starting (opens in your browser) ==="
.testvenv/bin/python -m studio.app
