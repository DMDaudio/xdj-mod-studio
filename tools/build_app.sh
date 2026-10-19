#!/bin/sh
# Build dist/XDJ-700 Emulator.app (self-contained; reads firmware builds,
# themes and the snapshot from this repo, whose path is baked in).
set -e
cd "$(dirname "$0")/.."
cc -O2 -shared -fPIC -o emu/_fastcore.so emu/fastcore.c -lm
mkdir -p build/emu-app
cp -f "$(brew --prefix sdl3)/lib/libSDL3.0.dylib" build/emu-app/libSDL3.dylib
printf 'REPO = %s\n' "\"$(pwd)\"" > emu/_buildinfo.py
.venv/bin/python -m PyInstaller --noconfirm XDJ700Emu.spec --distpath dist --workpath build/emu-app
codesign --force --deep -s - "dist/XDJ-700 Emulator.app"
echo "built dist/XDJ-700 Emulator.app"
