#!/bin/sh
# Build the on-deck theme switcher: build/themesw/ts.bin + ts.syms
# TS_BASE = code address (entry table at +0); TS_DATA = theme blob.
# Both need a read-only deck survey before any deck install.
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SH=$ROOT/work/sh-tools/bin
OUT=$ROOT/build/themesw
BASE=${TS_BASE:-0x09770000}
DATA=${TS_DATA:-0x09776000}
MSTNAME=${MSTNAME:?}
MSTAPP=${MSTAPP:?}
mkdir -p "$OUT"
cd "$ROOT/tools/themesw"
"$SH/sh-elf-gcc" -m4-nofpu -mdiv=call-div1 -ml -O2 -Wall -ffreestanding -fno-builtin \
  -DTS_DATA=${DATA}u -DMSTNAME=${MSTNAME}u -DMSTAPP=${MSTAPP}u \
  -ffunction-sections -fdata-sections -nostdlib -Wl,--gc-sections -Wl,-e,_ts_table \
  -Wl,--section-start=.entry=$BASE -Wl,-Ttext=$(printf '0x%X' $((BASE + 0x10))) \
  -o "$OUT/ts.elf" tsentry.S ts.c -lgcc
"$SH/sh-elf-objcopy" -O binary -j .entry -j .text -j .rodata -j .data "$OUT/ts.elf" "$OUT/ts.bin"
"$SH/sh-elf-nm" "$OUT/ts.elf" | grep -E " (_ts_|_frm|_to|_bits)" > "$OUT/ts.syms"
"$SH/sh-elf-size" "$OUT/ts.elf"
N=$("$SH/sh-elf-objdump" -d "$OUT/ts.elf" | awk -F'\t' 'NF>=3{print $3}' | grep -cE '^(f[a-z.]+|frchg|fschg)\s|fpscr|fpul' || true)
echo "FPU instructions: $N (must be 0)"; [ "$N" = 0 ]
END=$("$SH/sh-elf-nm" "$OUT/ts.elf" | awk '$3=="_end"{print $1}')
[ $((0x$END)) -le $((DATA)) ] || { echo "ERROR: code/bss (end 0x$END) reaches TS_DATA"; exit 1; }
cat "$OUT/ts.syms"
