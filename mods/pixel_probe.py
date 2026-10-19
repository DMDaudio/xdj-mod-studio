#!/usr/bin/env python3
"""mod: pixel_probe -- recolour a KNOWN on-screen colour, addressed by content.

    python3 mods/pixel_probe.py work [FROM565] [TO565]

THE IDEA THAT MAKES THIS DIFFERENT
Every previous colour attempt guessed at a table (32-bit palette: flashed,
nothing changed. RGB565 ramps: flashed, nothing changed). This one starts from
GROUND TRUTH instead.

tools/pngread.py reads the real screenshot the sim/ previews were built from --
the first actual on-screen colours this project has ever had. The panel grey
#2C2C2C is ~8% of the display. Its RGB565 form 0x2965 appears 270 times as
fully-literal pairs in block1, against a magnitude-matched random control of
median 26 / 90th percentile 52. That is 5x above the 90th percentile: real
enrichment, not the noise that sank the earlier hue searches.

Note this is NOT the 32-bit constant tested at 2.05 (which occurs exactly once
and changed nothing) and NOT a ramp. It is the raw 16-bit pixel value.

WHY IT IS CODEC-PROOF
We address by CONTENT, never by output offset. Which stream byte is a literal
is codec-independent; only where it LANDS depends on the unsolved match
semantics. Changing a literal makes the deck substitute the new value
everywhere that literal's descendants land -- and every one of those
destinations previously held this exact colour byte. So the edit is correct
under the deck's decoder even though ours is wrong.

CONTAINMENT
tools/sitefilter.py drops any site whose descendants reach a code-bearing 64 KB
window. 205 of 270 sites survive: 6,452 changed bytes, ZERO in code.

Default target is MAGENTA. Nothing on a stock XDJ-700 is magenta -- the PERFORM
button is purple in stock firmware and already caused one false positive here.
"""
import json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from litpatch import map_literals
from sitefilter import safe_sites

HDR = 0x40000


def apply(work="work", src565=0x2965, dst565=0xF81F):
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    src = img[HDR + 8:HDR + 8 + ln]
    dec, lit = map_literals(src)

    pat = struct.pack("<H", src565)
    sites = []; p = 0
    while True:
        p = dec.find(pat, p)
        if p < 0: break
        if p in lit and p + 1 in lit: sites.append(p)
        p += 1
    print(f"  {src565:#06x}: {len(sites)} fully-literal occurrences")

    good, st = safe_sites(src, dec, lit, sites)
    print(f"  containment: kept {st['kept']}, dropped {st['dropped']} "
          f"(would have put {st['code_bytes_avoided']} bytes into code)")
    print(f"  predicted changed output bytes: {st['changed']:,}, none in code")

    p = f"{work}/MAIN.blk01.litpatch.json"
    edits = json.load(open(p)) if os.path.exists(p) else {}
    lo, hi = dst565 & 0xFF, dst565 >> 8
    n = 0
    for s in good:
        if dec[s] != lo: edits[str(s)] = lo; n += 1
        if dec[s + 1] != hi: edits[str(s + 1)] = hi; n += 1
    json.dump(edits, open(p, "w"))
    print(f"  {src565:#06x} -> {dst565:#06x}   {n} literal bytes edited")


if __name__ == "__main__":
    a = sys.argv
    apply(a[1] if len(a) > 1 else "work",
          int(a[2], 0) if len(a) > 2 else 0x2965,
          int(a[3], 0) if len(a) > 3 else 0xF81F)
