#!/bin/sh
# Build the key-shift DSP code (ks.bin, DSP 0xC005F4C0) and the SH one-shot
# reload cave that carries it (build/keyshift/ksr.bin at KSR_BASE).
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
C6=$ROOT/work/tic6x-tools/bin
SH=$ROOT/work/sh-tools/bin
OUT=$ROOT/build/keyshift
BASE=${KSR_BASE:-0x09800000}
mkdir -p "$OUT"
cd "$ROOT/tools/keyshift_dsp"
"$C6/tic6x-elf-gcc" -Os -march=c674x -mlittle-endian -msdata=none -ffreestanding \
  -fno-builtin -nostdlib -ffunction-sections -c ks.c -o "$OUT/ks.o"
"$C6/tic6x-elf-ld" -T ks.ld "$OUT/ks.o" -o "$OUT/ks.elf"
"$C6/tic6x-elf-objcopy" -O binary "$OUT/ks.elf" "$OUT/ks.bin"
cp "$OUT/ks.bin" ks.bin; cp "$OUT/ks.elf" ks.elf
"$C6/tic6x-elf-nm" "$OUT/ks.elf" > "$OUT/ks.syms"
U=$("$C6/tic6x-elf-nm" -u "$OUT/ks.elf" | wc -l | tr -d ' '); [ "$U" = 0 ]
"$SH/sh-elf-gcc" -m4-nofpu -mdiv=call-div1 -ml -O2 -Wall -ffreestanding -fno-builtin \
  -ffunction-sections -fdata-sections -nostdlib -Wl,--gc-sections -Wl,-e,_ksr_entry \
  -Wl,--section-start=.entry=$BASE -Wl,-Ttext=$(printf '0x%X' $((BASE + 0x40))) \
  -o "$OUT/ksr.elf" ksrhook.S ksr.c -lgcc
"$SH/sh-elf-objcopy" -O binary -j .entry -j .text -j .rodata -j .data "$OUT/ksr.elf" "$OUT/ksr.bin"
"$SH/sh-elf-nm" "$OUT/ksr.elf" | grep -E " _(ksr|ks_)" > "$OUT/ksr.syms"
"$SH/sh-elf-size" "$OUT/ksr.elf"
N=$("$SH/sh-elf-objdump" -d "$OUT/ksr.elf" | awk -F'\t' 'NF>=3{print $3}' | grep -cE '^(f[a-z.]+|frchg|fschg)\s|fpscr|fpul' || true)
echo "FPU instructions: $N (must be 0)"; [ "$N" = 0 ]
cat "$OUT/ksr.syms"; ls -l "$OUT/ksr.bin" "$OUT/ks.bin"
