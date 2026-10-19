#!/bin/sh
# Build the xopus decoder twice from the same sources:
#   build/xopus/xopus_native   Mac CLI (test oracle)
#   build/xopus/xopus_sh.bin   SH-4 image, linked at $XO_BASE (default 0x0F000000,
#                              the emu test address), entry table first
# libopus 1.5.2 source: work/opus/opus-1.5.2 (sha256 65c1d2f7...a7ce1,
# downloads.xiph.org; BSD). Toolchain: tools/build_sh_gcc.sh.
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
O=$ROOT/work/opus/opus-1.5.2
OUT=$ROOT/build/xopus
SH=$ROOT/work/sh-tools/bin
BASE=${XO_BASE:-0x0F000000}
mkdir -p "$OUT"
SRC="$ROOT/tools/xopus/xopus.c src/opus.c src/opus_decoder.c src/extensions.c"
for f in $(sed -n '/^CELT_SOURCES = /,/^$/p' $O/celt_sources.mk | grep '\.c' | tr -d '\\') \
         $(sed -n '/^SILK_SOURCES = /,/^$/p' $O/silk_sources.mk | grep '\.c' | tr -d '\\') \
         $(sed -n '/^SILK_SOURCES_FIXED = /,/^$/p' $O/silk_sources.mk | grep '\.c' | tr -d '\\'); do
  SRC="$SRC $f"
done
DEFS="-DOPUS_BUILD -DFIXED_POINT -DDISABLE_FLOAT_API -DNONTHREADSAFE_PSEUDOSTACK \
  -DGLOBAL_STACK_SIZE=65536 -DOPUS_VERSION=\"1.5.2-xdj\""
INC="-I$O/include -I$O/celt -I$O/silk -I$O/silk/fixed -I$O/src -I$O"
cd "$O"
# Let the build choose libopus' 64-bit multiply shortcut: the Mac (LP64)
# would take it, the 32-bit SH does not, and the two round differently by
# 1 LSB. The oracle must take the deck's path (idempotent source patch).
grep -q "ifndef OPUS_FAST_INT64" celt/arch.h || \
  sed -i '' 's|^#if defined(__x86_64__) \|\| defined(__LP64__) \|\| defined(_WIN64)$|#ifdef OPUS_FAST_INT64\
/* set by the build (xdj700 mods) */\
#elif defined(__x86_64__) \|\| defined(__LP64__) \|\| defined(_WIN64)|' celt/arch.h
grep -q "ifdef OPUS_FAST_INT64" celt/arch.h
# native oracle (32-bit multiply path, like the deck)
cc -O2 -w -DOPUS_FAST_INT64=0 $DEFS $INC -o "$OUT/xopus_native" $SRC
# deck build: integer-only ABI, division without the FPU, unused code dropped
"$SH/sh-elf-gcc" -m4-nofpu -mdiv=call-div1 -ml -O2 -w -ffreestanding \
  -fno-builtin -ffunction-sections -fdata-sections -DXO_FREESTANDING \
  -I$ROOT/tools/xopus/libc \
  $DEFS $INC -nostdlib -Wl,--gc-sections -Wl,-e,_xo_entry \
  -Wl,--section-start=.entry=$BASE -Wl,-Ttext=$(printf '0x%X' $((BASE + 0x40))) \
  -o "$OUT/xopus_sh.elf" $SRC -lgcc
"$SH/sh-elf-objcopy" -O binary -j .entry -j .text -j .rodata -j .data "$OUT/xopus_sh.elf" "$OUT/xopus_sh.bin"
"$SH/sh-elf-size" "$OUT/xopus_sh.elf"
N=$("$SH/sh-elf-objdump" -d "$OUT/xopus_sh.elf" | awk -F'\t' 'NF>=3{print $3}' | grep -cE '^(f[a-z.]+|frchg|fschg)\s|fpscr|fpul' || true)
echo "FPU instructions in deck build: $N (must be 0)"
[ "$N" = 0 ]
