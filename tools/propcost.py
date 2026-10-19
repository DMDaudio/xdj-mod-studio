#!/usr/bin/env python3
"""propcost.py -- how many bytes does changing this palette entry disturb?

    python3 tools/propcost.py            # per-entry cost table

WHY THIS EXISTS
On 2026-09-05 a build with 222 direct edits and 1,099 propagated bytes put a
real XDJ-700 into MAIN CPU ERROR. A build with 3 direct and 0 propagated flashed
perfectly. Propagation, not edit count, is what breaks the deck.

A direct literal edit is safe under ANY match semantics -- the byte decodes to
itself. But if a later match COPIES that byte, where the copy lands depends on
the match semantics, which are unproven (CODEC.md). Our model's prediction and
the deck's behaviour disagreed, and the difference bricked it.

So before changing a colour, ask how many OTHER output bytes descend from the
same literal. Zero means the edit is as safe as the version test that worked.

METHOD
Decode while recording, for each output byte, whether it is a literal (and where
in the stream) or a match (and which earlier byte it copied). Resolve every byte
to its root literal, then count how many bytes share each root. An entry's cost
is the total fan-out of its four bytes, minus the four bytes themselves.

RESULT (94 entries, full-block count): see the table printed by this script.
The CHROME tables (greys, whites, near-blacks) are almost entirely free.
The ACCENT ramps are the expensive ones -- which is the opposite of what a
naive theme wants to change.
"""
import collections, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from litpatch import map_literals
import palette

N, F, THR, FILL = 4096, 18, 2, 0x20
# Fan-out must be counted over the WHOLE block. An earlier version truncated at
# 0xCC6000 and reported 0 propagation for entries that actually propagate 85
# bytes -- repack.py's real measurement caught the discrepancy. Memory is kept
# O(ring) rather than O(output) by tracking each ring slot's root literal
# instead of storing provenance for all 18.4M output bytes.


def analyse(work=None):
    """-> {palette_entry_offset: propagation_cost}, counted over the FULL block."""
    work = work or os.path.join(HERE, "..", "work")
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    src = img[0x40008:0x40008 + ln]

    rb = bytearray([FILL] * (N - F) + [0] * F)
    rroot = [-1] * N              # root literal of the byte in each ring slot
    fan = collections.Counter()
    r, p, n = N - F, 0, len(src)
    while p < n:
        fl = src[p]; p += 1
        for b in range(8):
            if p >= n:
                break
            if fl >> b & 1:
                c = src[p]
                rb[r] = c; rroot[r] = p
                fan[p] += 1
                p += 1; r = (r + 1) % N
            else:
                if p + 1 >= n:
                    break
                b1, b2 = src[p], src[p + 1]; p += 2
                i = b2 | ((b1 & 0xF0) << 4); j = (b1 & 0x0F) + THR + 1
                for k in range(j):
                    q = (i + k) % N
                    c = rb[q]; root = rroot[q]
                    rb[r] = c; rroot[r] = root
                    if root >= 0:
                        fan[root] += 1
                    r = (r + 1) % N

    dec, lit = map_literals(src)
    cost = {}
    for o, rgb, alpha, order, base in palette.entries(dec, lit):
        t = 0
        for k in range(4):
            st = lit.get(o + k)
            if st is None:
                t = -1; break
            t += fan.get(st, 1) - 1
        cost[o] = t
    return cost


if __name__ == "__main__":
    import os as _os
    sys.path.insert(0, _os.path.join(HERE, "..", "mods"))
    img = open(_os.path.join(HERE, "..", "work", "MAIN.img"), "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    dec, lit = map_literals(img[0x40008:0x40008 + ln])
    cost = analyse()
    by = collections.defaultdict(list)
    for o, rgb, a, order, base in palette.entries(dec, lit):
        by[base].append((o, rgb, cost.get(o, -1)))
    print(f"{'table':>10} {'n':>3} {'free':>5} {'<=8':>5}  contents")
    tf = tn = 0
    for base in sorted(by):
        v = by[base]
        free = sum(1 for _, _, c in v if c == 0)
        cheap = sum(1 for _, _, c in v if 0 <= c <= 8)
        tf += free; tn += len(v)
        lbl = next(l for b, n_, l in palette.TABLES if b == base)
        print(f"  {base:#08x} {len(v):>3} {free:>5} {cheap:>5}  {lbl}")
    print(f"\n  {tf} of {tn} entries cost ZERO propagation")
    print("\n  free entries by colour:")
    for base in sorted(by):
        s = " ".join(f"{r:02X}{g:02X}{b:02X}" for _, (r, g, b), c in by[base] if c == 0)
        if s:
            print(f"    {base:#08x}  {s}")
