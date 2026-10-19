#!/usr/bin/env python3
"""ramps.py -- find literal RGB565 colour ramps in block1.

WHY THIS EXISTS
The 32-bit "palette" at 0xCC2A70 was a guess, and it failed its hardware test:
#2C2C2C occurs exactly ONCE as a 32-bit constant in 18.4 MB, we changed it
(build probe_green_v2.05, which booted -- so the edit WAS live), and the 8.07%
of the screen painted that colour did not move. The panel is RGB565.

TWO FILTERS THAT MATTER
 - Multi-channel: at least two of R,G,B must move, in the same direction.
   Without this the census is swamped by runs of consecutive u16 integers
   (index/identity LUTs), which trivially have monotonic luminance and look
   like "ramps" where only the blue channel steps 00 08 10 18 20 29.
 - step != 1, for the same reason.

CODEC-INDEPENDENT BY CONSTRUCTION
Output offsets are NOT trustworthy: which output byte a literal becomes depends
on the match-length formula, and our two candidates drift 231 KB apart over
block1. But *which stream byte is a literal* IS codec-independent (a literal
costs 1 stream byte, a match 2, always). So edits are addressed by CONTENT:
patch the literal bytes that spell a ramp and, wherever that ramp lives in the
deck's memory, it changes. What we lose is the ability to predict which UI
element moves -- that is exactly what a probe is for.

    ramps.py [MIN_ENTRIES]
"""
import os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from litpatch import map_literals

def from565(v):
    r = (v >> 11) & 31; g = (v >> 5) & 63; b = v & 31
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)

def to565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

def find(dec, lit, minlen=8):
    """-> [(output_offset, [u16, ...])] for each literal multi-channel ramp."""
    n = len(dec); res = []; i = 0
    while i < n - 3:
        if not (i in lit and i + 1 in lit):
            i += 1; continue
        vals = []; o = i
        while o < n - 1 and o in lit and o + 1 in lit:
            vals.append(dec[o] | (dec[o + 1] << 8)); o += 2
        s = 0
        for k in range(1, len(vals) + 1):
            brk = True
            if k < len(vals):
                a, b = from565(vals[k - 1]), from565(vals[k])
                d = [b[t] - a[t] for t in range(3)]
                brk = not (sum(1 for x in d if x) >= 2
                           and (all(x >= 0 for x in d) or all(x <= 0 for x in d))
                           and abs(vals[k] - vals[k - 1]) != 1
                           and max(abs(x) for x in d) <= 40)
            if brk:
                if k - s >= minlen:
                    res.append((i + s * 2, vals[s:k]))
                s = k
        i = o if len(vals) >= 2 else i + 2
    return res

def load(work=None):
    work = work or os.path.join(HERE, "..", "work")
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    return map_literals(img[0x40008:0x40008 + ln])

def family(vals):
    """Rough hue label so identical ramps group together."""
    r, g, b = from565(vals[len(vals) // 2])
    if max(r, g, b) - min(r, g, b) <= 14: return "grey"
    if r >= g >= b: return "orange/amber"
    if b >= g >= r: return "blue"
    if b >= r and g >= r: return "teal/cyan"
    if g >= r and g >= b: return "green"
    return "other"

if __name__ == "__main__":
    mn = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    dec, lit = load()
    res = find(dec, lit, mn)
    from collections import defaultdict
    g = defaultdict(list)
    for off, vals in res:
        g[(family(vals), tuple(vals))].append(off)
    print(f"{len(res)} literal RGB565 ramps >= {mn} entries, "
          f"{len(g)} distinct\n")
    rows = sorted(g.items(), key=lambda kv: (-len(kv[0][1]), -len(kv[1])))
    tot = 0
    for (fam, vals), offs in rows:
        c = " ".join("%02X%02X%02X" % from565(v) for v in vals[:8])
        tot += len(offs) * len(vals) * 2
        print(f"  {fam:<12} {len(vals):>3}e x{len(offs):<3} {c}"
              f"{' ...' if len(vals) > 8 else ''}")
        print(f"               at {' '.join(f'{o:#x}' for o in offs[:6])}"
              f"{' ...' if len(offs) > 6 else ''}")
    print(f"\n  {tot} literal bytes of colour ramp in total")
