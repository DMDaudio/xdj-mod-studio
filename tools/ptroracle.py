#!/usr/bin/env python3
"""ptroracle.py -- NEGATIVE RESULT, kept as documentation.

An attempt at the non-saturating second oracle CODEC.md asks for. It does not
work: 12,423 literal in-range pointers, 0 land on a string start, and a base
sweep produces no real peak. The string table is itself ~80% match-corrupted,
so the pointers have nothing to hit. See FEATURES.md section 5.

The idea, for anyone tempted to retry it: pointer coherence.

CODEC.md asks for a metric that does not saturate the way the register-constant
oracle does. This is one.

Key observation: a 4-byte pointer stored as LZSS literals has the SAME VALUE in
every decode -- literals are codec-independent. What changes between decodes is
*where its target lands*, because different match lengths shift every output
offset. So:

    a decode is right (and the load base is right) only if literal pointers
    land on plausible targets -- string starts, 4-aligned words, and so on.

That is a joint test of the codec AND the load address, it uses data the
register oracle never touches, and it cannot saturate the same way: every
literal pointer in the image votes.

    ptroracle.py                       compare V1 vs OKU at base 0x08000000
    ptroracle.py --sweep STEP RANGE    sweep the load base
"""
import os, struct, sys

REC = 6
BASE = 0x08000000
PRINT = bytes(range(0x20, 0x7F))
PSET = set(PRINT)


def load(binp, provp):
    img = open(binp, 'rb').read()
    prov = open(provp, 'rb').read()
    return img, prov[0::REC]


def literal_ptrs(img, kind, base, span):
    """all-literal u32s whose value falls inside [base, base+span)"""
    out = []
    n = len(img)
    for o in range(0, n - 3, 2):
        if kind[o] or kind[o + 1] or kind[o + 2] or kind[o + 3]:
            continue
        v = img[o] | (img[o + 1] << 8) | (img[o + 2] << 16) | (img[o + 3] << 24)
        if base <= v < base + span:
            out.append((o, v))
    return out


def strstart(img, kind, t, minlen=4):
    """does offset t begin a NUL-terminated printable run of >= minlen,
    entirely literal?"""
    n = len(img)
    if not (0 < t < n - minlen):
        return False
    if img[t - 1] != 0:
        return False
    L = 0
    while t + L < n and img[t + L] in PSET and L < 64:
        if kind[t + L]:
            return False
        L += 1
    return L >= minlen and t + L < n and img[t + L] == 0


def score(img, kind, base, ptrs):
    hit = 0
    for _, v in ptrs:
        if strstart(img, kind, v - base):
            hit += 1
    return hit


def main():
    sets = [('700 V1 ', 'work/V1.blk01.bin', 'work/V1.blk01.prov'),
            ('700 OKU', 'work/OKU.blk01.bin', 'work/OKU.blk01.prov')]
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    print(f"{'decode':<9} {'lit ptrs':>9} {'-> string start':>16} {'rate':>7} "
          f"{'control(+0x1000)':>17} {'ratio':>7}")
    print('-' * 74)
    for lbl, b, p in sets:
        img, kind = load(os.path.join(here, b), os.path.join(here, p))
        ptrs = literal_ptrs(img, kind, BASE, len(img))
        h = score(img, kind, BASE, ptrs)
        c = score(img, kind, BASE + 0x1000, ptrs)      # same pointers, wrong base
        r = h / c if c else float('inf')
        print(f'{lbl:<9} {len(ptrs):>9} {h:>16} {100.0*h/max(len(ptrs),1):>6.2f}% '
              f'{c:>17} {r:>6.1f}x')


if __name__ == '__main__':
    main()
