#!/bin/bash
# build.sh [mod ...] -- stock firmware -> modded .UPD, preflighted.
#   ./build.sh                 rebuild stock (proves the toolchain)
#   ./build.sh version_tag     apply mods/version_tag.py
set -e
cd "$(dirname "$0")"
STOCK="${STOCK:-$HOME/Downloads/cdj3k-mods-0.1.0/XDJ700.UPD}"
OUT="${OUT:-build/XDJ700.UPD}"
mkdir -p build
[ -x tools/lzss ] || (cd tools && cc -O2 -o lzss lzss.c)
echo "==> unpacking stock"
(cd tools && ./setup_work.sh "$STOCK" ../work) >/dev/null
for m in "$@"; do
  echo "==> mod: $m"
  python3 "mods/$m.py" work
done
echo "==> repacking"
(cd tools && python3 repack.py ../work "../$OUT")
echo "==> preflight"
(cd tools && python3 preflight.py "../$OUT" "$STOCK")
