#!/bin/sh
# Build a freestanding TI C6x C cross compiler (tic6x-elf) for patches to the
# deck's DSP program (live key shift). Everything stays in work/ (gitignored).
# Sources: gcc-14.2.0 (same tarball + sha256 as tools/build_sh_gcc.sh; reuses
# a copy from work/sh-build or ~/Downloads/xdj700-archive), binutils already
# installed in work/tic6x-tools (tools/build_tic6x_objdump.sh).
# Use: tic6x-elf-gcc -march=c674x -mlittle-endian -O2 -ffreestanding -nostdlib
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/c6x-gcc-build
P=$ROOT/work/tic6x-tools
mkdir -p "$W/gcc"
cd "$W"
if [ ! -f gcc-14.2.0.tar.xz ]; then
  for s in "$ROOT/work/sh-build" "$HOME/Downloads/xdj700-archive/work/sh-build"; do
    [ -f "$s/gcc-14.2.0.tar.xz" ] && cp "$s/gcc-14.2.0.tar.xz" . && break
  done
fi
[ -f gcc-14.2.0.tar.xz ] || curl -sSLO https://ftp.gnu.org/gnu/gcc/gcc-14.2.0/gcc-14.2.0.tar.xz
echo "a7b39bc69cbf9e25826c5a60ab26477001f7c08d85cec04bc0e29cabed6f3cc9  gcc-14.2.0.tar.xz" | shasum -a 256 -c -
[ -d gcc-14.2.0 ] || tar xf gcc-14.2.0.tar.xz
HB=/opt/homebrew/opt
cd gcc
PATH="$P/bin:$PATH" ../gcc-14.2.0/configure --target=tic6x-elf --prefix="$P" \
  --enable-languages=c --without-headers --with-newlib --disable-nls \
  --disable-shared --disable-threads --disable-libssp --disable-libquadmath \
  --disable-libgomp --disable-libatomic --disable-multilib \
  --with-arch=c674x \
  --with-gmp=$HB/gmp --with-mpfr=$HB/mpfr --with-mpc=$HB/libmpc \
  --with-isl=$HB/isl --with-system-zlib >cfg.log
PATH="$P/bin:$PATH" make -j8 MAKEINFO=true all-gcc >make.log
PATH="$P/bin:$PATH" make MAKEINFO=true install-gcc >inst.log
echo OK "$P/bin/tic6x-elf-gcc"
