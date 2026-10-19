#!/bin/sh
# xflac (dr_flac) twice from one source: build/xflac/xflac_native (Mac
# oracle) + build/xflac/xflac_sh.bin linked at $XF_BASE (default 0x0F000000,
# the emulator test address). Deck toolchain: tools/build_sh_gcc.sh.
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=$ROOT/build/xflac
SH=$ROOT/work/sh-tools/bin
BASE=${XF_BASE:-0x0F000000}
INC="-I$ROOT/work/drflac"
mkdir -p "$OUT"
cc -O2 -w $INC -o "$OUT/xflac_native" "$ROOT/tools/xflac/xflac.c"
"$SH/sh-elf-gcc" -m4-nofpu -mdiv=call-div1 -ml -O2 -w -ffreestanding -fno-builtin \
  -ffunction-sections -fdata-sections -DXF_FREESTANDING -I$ROOT/tools/xopus/libc $INC \
  -nostdlib -Wl,--gc-sections -Wl,-e,_xf_entry \
  -Wl,--section-start=.entry=$BASE -Wl,-Ttext=$(printf '0x%X' $((BASE + 0x40))) \
  -o "$OUT/xflac_sh.elf" "$ROOT/tools/xflac/xflac.c" -lgcc
"$SH/sh-elf-objcopy" -O binary -j .entry -j .text -j .rodata -j .data "$OUT/xflac_sh.elf" "$OUT/xflac_sh.bin"
"$SH/sh-elf-size" "$OUT/xflac_sh.elf"
N=$("$SH/sh-elf-objdump" -d "$OUT/xflac_sh.elf" | awk -F'\t' 'NF>=3{print $3}' | grep -cE '^(f[a-z.]+|frchg|fschg)\s|fpscr|fpul' || true)
echo "FPU instructions in deck build: $N (must be 0)"; [ "$N" = 0 ]
