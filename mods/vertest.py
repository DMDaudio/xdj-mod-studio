#!/usr/bin/env python3
"""mod: vertest -- change the version string UTILITY displays.

    python3 mods/vertest.py work [9.99]

THE POINT
This is the unambiguous "did the flash actually take?" test. Colours are a bad
first test -- the PERFORM button is purple in STOCK firmware, which already
caused one false positive. On-screen labels are no good either: QUANTIZE,
PERFORM, SLIP, HOT CUE and the rest do not appear as text anywhere in the
payload, so they are rendered as sprites.

The version block in block1 at 0x524 IS text, IS literal-encoded, and UTILITY
puts it on screen:

    PIONEER DJ ... XDJ-700 ... 1.15 ... 20241002 ... <- end of version->

So: flash, open UTILITY, read the version.
    shows 9.99  -> the deck accepted our rebuilt .UPD. Everything downstream
                   (container, CRC, literal patching) is proven.
    shows 1.15  -> it did not take, and nothing we have built has ever run.

Same length, literal-only, no recompression, stream length unchanged.
"""
import json, os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from litpatch import map_literals

BLK, HDR = 1, 0x40000


def apply(work, new="9.99"):
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    i = dec.find(b"PIONEER")
    if i < 0:
        sys.exit("version block not found")
    j = dec.find(b"1.15", i, i + 200)
    if j < 0:
        sys.exit("version string not found in the version block")
    if len(new) != 4:
        sys.exit("replacement must be 4 chars, e.g. 9.99")
    miss = [j + k for k in range(4) if j + k not in lit]
    if miss:
        sys.exit(f"not literal-encoded at {[hex(x) for x in miss]}")

    p = f"{work}/MAIN.blk{BLK:02d}.litpatch.json"
    edits = json.load(open(p)) if os.path.exists(p) else {}
    n = 0
    for k in range(4):
        if new[k] != chr(dec[j + k]):
            edits[str(j + k)] = ord(new[k]); n += 1
    json.dump(edits, open(p, "w"))
    print(f"  version '1.15' -> {new!r} at blk1 {j:#x}  ({n} bytes)")
    print(f"  check it in UTILITY after flashing")


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "work",
          sys.argv[2] if len(sys.argv) > 2 else "9.99")
