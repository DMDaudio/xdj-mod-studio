#!/usr/bin/env python3
"""mod: data_probe -- invert the literal bytes of the two pure-data regions.

    python3 mods/data_probe.py work

THE QUESTION
Does ANY literal-encoded data reach the screen at all?

Two palette candidates have now been flashed and both booted and changed
nothing (PALETTE.md). The structural explanation is that block1 is only 19%
literal and the live palette is in the 81% we cannot touch. That explanation is
plausible but UNTESTED -- and it is the thing standing between us and themes,
so it deserves a direct experiment rather than an inference.

THE TARGETS
Exactly two 64 KB windows in 18.4 MB are >90% literal:

    0xcd0000   99.4% literal, entropy 7.63
    0x140000   98.9% literal, entropy 7.85

99% literal means LZSS found almost no matches -- the data is incompressible.
Flat UI sprites compress well; already-compressed or high-entropy assets do not.
So these are the two places where a large, coherent, fully-patchable asset could
be hiding, and 0xcd0000 is where the original 32-bit "palette" was found.

WHY THIS IS SAFER THAN IT LOOKS
Both windows are provably NOT SH-4 code. Per 64K halfwords, against the
uncompressed boot stub as ground truth:

    boot stub (real code)   rts=376  sts.l pr=167  lds.l pr=167  nop=1128
    0xcd0000                rts=  0  sts.l pr=  0  lds.l pr=  0  nop=   0
    0x140000                rts=  0  sts.l pr=  6  lds.l pr=  2  nop=   0

Zero control-flow structure. Corrupting these cannot crash the CPU by executing
garbage; the worst case is that firmware reads bad data.

READING THE RESULT
    screen visibly garbled/recoloured -> literal data DOES reach the display.
        Bisect from here: ~17 flashes halves 128 KB down to ~1 KB, and far
        fewer if the change is localised to one element.
    boots, nothing changes           -> these regions are not display data.
        Combined with the two dead palettes, that is strong support for the
        "the palette is in the 81%" conclusion, and themes really are
        codec-blocked.
    MAIN CPU ERROR                   -> the data is load-bearing some other way.
        Recover and we still learned something.

All three outcomes are worth a flash, which is the point.
"""
import json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from litpatch import map_literals

HDR = 0x40000
WINDOWS = [(0xcd0000, 0xce0000), (0x140000, 0x150000)]

# The two-window probe covers only 3.7% of block1's literals, so a
# "boots normal" result would prove almost nothing. broad_windows() selects
# every 64 KB window with NO SH-4 code signature, which reaches 72.1% of them
# (2,517,000 bytes across 225 windows). Thresholds are calibrated against the
# uncompressed boot stub: rts=376, sts.l pr=167 per 64K halfwords.
def broad_windows(dec, lit, W=1 << 16):
    out = []
    n = len(dec)
    for a in range(0, n, W):
        b = min(a + W, n)
        rts = sts = 0
        for p in range(a, b - 1, 2):
            w = dec[p] | (dec[p + 1] << 8)
            if w == 0x000B: rts += 1
            elif w == 0x4F22: sts += 1
        hw = (b - a) // 2
        if (rts * 65536 // hw) >= 40 or (sts * 65536 // hw) >= 20:
            continue
        if any(o in lit for o in range(a, b)):
            out.append((a, b))
    return out


def apply(work="work", windows=None, broad=False):
    windows = windows or WINDOWS
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    if broad:
        windows = broad_windows(dec, lit)
        print(f"  broad mode: {len(windows)} code-free windows")

    p = f"{work}/MAIN.blk01.litpatch.json"
    edits = json.load(open(p)) if os.path.exists(p) else {}
    n = 0
    for lo, hi in windows:
        c = 0
        for o in range(lo, hi):
            if o in lit:
                edits[str(o)] = dec[o] ^ 0xFF; c += 1; n += 1
        if not broad:
            print(f"  {lo:#08x}-{hi:#08x}: {c:,} literal bytes inverted "
                  f"({100*c/(hi-lo):.1f}% of the window)")
    json.dump(edits, open(p, "w"))
    print(f"  {n:,} bytes total")


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "work",
          broad="--broad" in sys.argv)
