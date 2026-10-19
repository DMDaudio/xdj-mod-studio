#!/bin/sh
# Launch the XDJ-700 emulator on macOS.
# Usage: ./run_emu.sh [--upd XDJ700.UPD] [--mock]
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/.venv/bin/python" -m emu.main "$@"
