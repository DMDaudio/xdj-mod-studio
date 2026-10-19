#!/bin/sh
# PROTOTYPE build of the 8-pad hot cue view: build/hotcue8grid/hg.bin + hg.syms
# HG_BASE is an emulator-only default; a deck install needs a read-only
# deck survey of the range first (CLAUDE.md section 75/77 rule).
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SH=$ROOT/work/sh-tools/bin
OUT=$ROOT/build/hotcue8grid
BASE=${HG_BASE:-0x09850000}
mkdir -p "$OUT"
cd "$ROOT/tools/hotcue8grid"
[ -f ../hotcue8/hc_glyphs.h ] || (cd ../hotcue8 && "$ROOT/.venv/bin/python" mkglyphs.py >/dev/null)
"$SH/sh-elf-gcc" -m4-nofpu -mdiv=call-div1 -ml -O2 -Wall -ffreestanding -fno-builtin \
  -DPST=${PST:?}u -DHCPAGE=${HCPAGE:?}u -DMSOFF=${MSOFF:?}u \
  -ffunction-sections -fdata-sections -nostdlib -Wl,--gc-sections -Wl,-e,_hg_entry \
  -Wl,--section-start=.entry=$BASE -Wl,-Ttext=$(printf '0x%X' $((BASE + 0x80))) \
  -o "$OUT/hg.elf" hghook.S hg.c -lgcc
"$SH/sh-elf-objcopy" -O binary -j .entry -j .text -j .rodata -j .data "$OUT/hg.elf" "$OUT/hg.bin"
"$SH/sh-elf-nm" "$OUT/hg.elf" | grep -E " _hg_" > "$OUT/hg.syms"
"$SH/sh-elf-size" "$OUT/hg.elf"
N=$("$SH/sh-elf-objdump" -d "$OUT/hg.elf" | awk -F'\t' 'NF>=3{print $3}' | grep -cE '^(f[a-z.]+|frchg|fschg)\s|fpscr|fpul' || true)
echo "FPU instructions: $N (must be 0)"; [ "$N" = 0 ]
"$SH/sh-elf-objdump" -d "$OUT/hg.elf" > "$OUT/hg.dis"
cat "$OUT/hg.syms"
