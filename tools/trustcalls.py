#!/usr/bin/env python3
"""trustcalls.py -- the call graph you are allowed to believe.

Builds indirect-call edges `mov.l @(disp,PC),Rn ... jsr @Rn` but keeps ONLY
the ones where every byte involved is LZSS-literal-derived:

  * the `mov.l @(disp,PC),Rn` instruction        (2 bytes, both literal)
  * the 4-byte pool constant it loads            (all 4 literal)
  * the `jsr @Rn` instruction                    (2 bytes, both literal)

Literals decode to themselves under ANY match semantics, so an edge that
passes is independent of the unsolved codec (CODEC.md). Everything else in a
disassembly of a compressed region is, at best, unverified.

    trustcalls.py [--all] [LO HI]

Prints: call site, target, whether the target looks like a function entry,
and the local literal fraction at both ends.
"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provmap import Prov, BASE

WINDOW = 12          # halfwords to look back for the register load


def lit(P, o, n):
    return all(P.kind(x) == 0 for x in range(o, o + n))


def prologue(img, a):
    """SH-4 function entries almost always push something first."""
    w = struct.unpack_from('<H', img, a)[0]
    return (w & 0xF0FF) == 0x20F6 or w == 0x4F22 or (w & 0xF0FF) == 0x4022


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    P = Prov()
    img = P.img
    lo = int(args[0], 0) if args else 0
    hi = int(args[1], 0) if len(args) > 1 else len(img)
    if lo >= BASE: lo -= BASE
    if hi >= BASE: hi -= BASE

    print(f"{'call site':>10} {'reg':>4} {'target':>10} {'entry?':>8} "
          f"{'lit%@site':>10} {'lit%@tgt':>9}")
    print('-' * 60)
    found = 0
    for p in range(lo, min(hi, len(img) - 1), 2):
        w = img[p] | (img[p + 1] << 8)
        if (w & 0xF0FF) != 0x400B:            # jsr @Rn
            continue
        if not lit(P, p, 2):
            continue
        rn = (w >> 8) & 0xF
        for k in range(1, WINDOW):
            q = p - 2 * k
            if q < 0:
                break
            v = img[q] | (img[q + 1] << 8)
            if (v & 0xF000) != 0xD000 or ((v >> 8) & 0xF) != rn:
                continue
            if not lit(P, q, 2):
                break
            a = ((q + 4) & ~3) + (v & 0xFF) * 4
            if not (0 <= a < len(img) - 3 and lit(P, a, 4)):
                break
            tgt = struct.unpack_from('<I', img, a)[0]
            off = tgt - 0x08000000
            if not (0 <= off < len(img)):
                break
            found += 1
            print(f"{BASE+p:#010x} r{rn:<3} {tgt:#010x} "
                  f"{'YES' if prologue(img, off) else '-':>8} "
                  f"{P.litfrac(max(0,p-256),p+256)*100:10.1f} "
                  f"{P.litfrac(max(0,off-256),off+256)*100:9.1f}")
            break
    print(f"\n{found} fully-literal indirect-call edges "
          f"in block1 {BASE+lo:#x}-{BASE+hi:#x}")


if __name__ == '__main__':
    main()
