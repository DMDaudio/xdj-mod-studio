#!/usr/bin/env python3
"""mod: skin_text -- demo skin pack via safe literal-only UI label swaps.

Every target below was verified literal-encoded in the stock LZSS stream,
so the deck reproduces each replacement exactly with no recompression.
Same-length replacements only.
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from litpatch import map_literals

SWAPS = [
    (b"VINYL BRAKE", b"VINYL BRK!!"),
    (b"BROWSE", b"BR0WSE"),
    (b"TEMPO", b"TEMP0"),
    (b"MASTER", b"MAST3R"),
    (b"UTILITY", b"UT1L1TY"),
    (b"PLAYER", b"PLAY3R"),
]
BLK, HDR = 1, 0x40000


def apply(work):
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    edits = {}
    for target, new in SWAPS:
        assert len(target) == len(new), f"{target!r} length"
        o = dec.find(target)
        if o < 0:
            sys.exit(f"{target!r} not found")
        miss = [o + k for k in range(len(target)) if o + k not in lit]
        if miss:
            sys.exit(f"{target!r} not literal at {[hex(x) for x in miss]}")
        for k in range(len(target)):
            if new[k] != target[k]:
                if o + k in edits and edits[o + k] != new[k]:
                    sys.exit(f"overlap at {o+k:#x}")
                edits[o + k] = new[k]
        print(f"  {target.decode()!r} -> {new.decode()!r} at {o:#x}")
    p = f"{work}/MAIN.blk{BLK:02d}.litpatch.json"
    json.dump({str(k): v for k, v in edits.items()}, open(p, "w"))
    print(f"  total {len(edits)} bytes")


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "work")
