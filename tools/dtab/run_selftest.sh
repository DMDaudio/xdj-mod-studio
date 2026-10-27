#!/bin/sh
# decode-once table self-test (see selftest.c)
set -e
cd "$(dirname "$0")"
OUT="${TMPDIR:-/tmp}/dtab_selftest"
cc -O2 -o "$OUT" selftest.c -lm
"$OUT" "${1:-64}"
