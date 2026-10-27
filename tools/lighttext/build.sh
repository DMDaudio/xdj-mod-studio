#!/bin/sh
# Light-mode text colour wrapper: build/lighttext/lt.bin + lt.syms at LT_BASE
# (0x09792000: gap between the 8-hot-cue module 0x09790000..0x09790D08 and
# the UTILITY rows 0x09794000; deck-zero in idle/loaded/playing, sec. 75/77).
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SH=$ROOT/work/sh-tools/bin
OUT=$ROOT/build/lighttext
BASE=${LT_BASE:-0x09792000}
mkdir -p "$OUT"
cd "$ROOT/tools/lighttext"
TS_DATA=$("$ROOT/.venv/bin/python" -c "
import sys; sys.path[:0] = ['$ROOT/tools', '$ROOT', '$ROOT/mods']
import theme_switch as TS; print(hex(TS.TS_DATA))")
"$SH/sh-elf-gcc" -DTS_DATA=${TS_DATA}u -m4-nofpu -mdiv=call-div1 -ml -O2 -Wall -ffreestanding -fno-builtin \
  -ffunction-sections -fdata-sections -nostdlib -Wl,--gc-sections -Wl,-e,_lt_head \
  -Wl,--section-start=.entry=$BASE -Wl,-Ttext=$(printf '0x%X' $((BASE + 0x20))) \
  -o "$OUT/lt.elf" ltentry.S lt.c -lgcc
"$SH/sh-elf-objcopy" -O binary -j .entry -j .text -j .rodata -j .data "$OUT/lt.elf" "$OUT/lt.bin"
"$SH/sh-elf-nm" "$OUT/lt.elf" | grep -E " _(lt_|end$)" > "$OUT/lt.syms"
"$SH/sh-elf-size" "$OUT/lt.elf"
END=$("$SH/sh-elf-nm" "$OUT/lt.elf" | awk '$3=="_end"{print $1}')
[ $((0x$END)) -le $((0x09794000)) ] || { echo "ERROR: end 0x$END reaches UTILITY"; exit 1; }
cat "$OUT/lt.syms"
