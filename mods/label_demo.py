#!/usr/bin/env python3
"""mod: label_demo -- retarget a literal-encoded UI label (theme/text path).

Proves UI text is patchable via the safe literal-only path: the bytes for
'VINYL BRAKE' were emitted as LZSS literals, so the deck reproduces the
replacement exactly without recompression. Same-length only.
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from litpatch import map_literals

TARGET = b"VINYL BRAKE"
NEW = b"VINYL BRK!!"
BLK, HDR = 1, 0x40000


def apply(work):
    assert len(TARGET) == len(NEW), "replacement must be the same length"
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    o = dec.find(TARGET)
    if o < 0:
        sys.exit(f"{TARGET!r} not found")
    miss = [o + k for k in range(len(TARGET)) if o + k not in lit]
    if miss:
        sys.exit(f"not literal-encoded at {[hex(x) for x in miss]}")
    edits = {o + k: NEW[k] for k in range(len(NEW)) if NEW[k] != TARGET[k]}
    p = f"{work}/MAIN.blk{BLK:02d}.litpatch.json"
    json.dump({str(k): v for k, v in edits.items()}, open(p, "w"))
    print(f"  {TARGET.decode()!r} -> {NEW.decode()!r} at {o:#x} "
          f"({len(edits)} bytes, all literal-encoded)")


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "work")
