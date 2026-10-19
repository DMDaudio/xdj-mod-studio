#!/bin/sh
# Build the LIVE key-shift installer cave (no DSP reset): build/keyshift/ksh.bin
# at KSH_BASE (default 0x09800000, the same deck-surveyed area ksr used).
# Needs ks.bin (run build_ksr.sh first; it builds the DSP side).
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SH=$ROOT/work/sh-tools/bin
OUT=$ROOT/build/keyshift
BASE=${KSH_BASE:-0x09800000}
cd "$ROOT/tools/keyshift_dsp"
[ -f ks.bin ] || sh ./build_ksr.sh
PBUF=$(awk '$3=="KS"{print "0x"$1}' "$OUT/ks.syms")
[ -n "$PBUF" ] || { echo "KS symbol missing (run build_ksr.sh)"; exit 1; }
"$SH/sh-elf-gcc" -DPBUF=$PBUF -m4-nofpu -mdiv=call-div1 -ml -O2 -Wall -ffreestanding -fno-builtin \
  -ffunction-sections -fdata-sections -nostdlib -Wl,--gc-sections -Wl,-e,_ksh_entry \
  -Wl,--section-start=.entry=$BASE -Wl,-Ttext=$(printf '0x%X' $((BASE + 0x40))) \
  -o "$OUT/ksh.elf" kshhook.S ksh.c -lgcc
"$SH/sh-elf-objcopy" -O binary -j .entry -j .text -j .rodata -j .data "$OUT/ksh.elf" "$OUT/ksh.bin"
"$SH/sh-elf-nm" "$OUT/ksh.elf" | grep -E " _(ksh|ks_)" > "$OUT/ksh.syms"
"$SH/sh-elf-size" "$OUT/ksh.elf"
N=$("$SH/sh-elf-objdump" -d "$OUT/ksh.elf" | awk -F'\t' 'NF>=3{print $3}' | grep -cE '^(f[a-z.]+|frchg|fschg)\s|fpscr|fpul' || true)
echo "FPU instructions: $N (must be 0)"; [ "$N" = 0 ]
cat "$OUT/ksh.syms"; ls -l "$OUT/ksh.bin"
