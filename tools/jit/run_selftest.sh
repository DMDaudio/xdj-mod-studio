#!/bin/sh
# SH-4 JIT self-tests: encoder vs assembler, then random blocks vs stage 1
set -e
cd "$(dirname "$0")"
python3 a64enc_test.py
OUT="${TMPDIR:-/tmp}/jit_selftest"
cc -O2 -o "$OUT" jit_selftest.c -lm
"$OUT" "${1:-20000}"
"$OUT" "${1:-20000}" clean
