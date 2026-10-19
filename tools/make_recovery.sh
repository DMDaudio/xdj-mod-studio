#!/bin/bash
# make_recovery.sh VERSION [OUT] -- stock payload, arbitrary banner.
#
# Every flash must raise the version, so probes and recoveries interleave up a
# ladder: probe 2.05 -> brick -> recover 2.10 -> probe 2.15 -> ...
# A recovery file only works if its banner is ABOVE whatever the deck is on, so
# generate one just above each probe rather than burning 9.99, which is the
# ceiling of the 4-character field and cannot be superseded.
set -e
cd "$(dirname "$0")/.."
ROOT="$PWD"
V="${1:?usage: make_recovery.sh 2.10 [out.UPD]}"
OUT="${2:-$ROOT/build/RECOVER_stock_v${V/./}.UPD}"
STOCK="$(realpath "${XDJ_STOCK:-$ROOT/build/RECOVER_stock_v199.UPD}")"
export XDJ_STOCK="$STOCK"
W=$(mktemp -d)
(cd tools && ./setup_work.sh "$STOCK" "$W") >/dev/null
python3 mods/bump_version.py "$W" "$V"
(cd tools && python3 repack.py "$W" "$OUT")
(cd tools && python3 preflight.py "$OUT" "$STOCK") | tail -2
rm -rf "$W"
echo "wrote $OUT  (stock payload, banner $V)"
