#!/usr/bin/env python3
"""codesanity.py -- is a region real SH-4 code, or plausible-looking garbage?

Three region-level tests, all calibrated against the UNCOMPRESSED boot stub
(work/MAIN.img 0x0-0xC40), which is ground-truth SH-4 with no decode involved:

 1. pool plausibility -- what fraction of `mov.l @(disp,PC),Rn` constants are
    plausible values (SH7724 peripheral space 0xA4/0xFE, the 0x08xxxxxx /
    0xA8xxxxxx image space, or small ints)?  Real code: nearly all.
    Garbage: chance (~a few %).
 2. control-flow density -- bsr/bra/jsr/rts per 100 halfwords, vs the
    1-in-16 rate you get from random halfwords for the 0xB000 (bsr/bra) block.
 3. literal fraction from the LZSS provenance map (0 for uncompressed input).

    codesanity.py                       run the standard comparison
    codesanity.py START END [label]     score one block1 region
"""
import os, struct, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provmap import Prov, BASE
import capstone

MD = capstone.Cs(capstone.CS_ARCH_SH,
                 capstone.CS_MODE_SH4 | capstone.CS_MODE_LITTLE_ENDIAN)


def plausible(v):
    if v < 0x10000:                       # small ints / bitmasks
        return True
    if 0xA4000000 <= v <= 0xA4FFFFFF:     # SH7724 peripheral (P2 alias)
        return True
    if 0xFE000000 <= v <= 0xFFFFFFFF and (v & 0xFFFF0000) != 0xFFFF0000:
        return True                       # on-chip peripheral space
    if 0x08000000 <= v <= 0x0FFFFFFF:     # physical image/RAM
        return True
    if 0xA8000000 <= v <= 0xAFFFFFFF:     # P2 alias of the above
        return True
    if 0x0C000000 <= v <= 0x0FFFFFFF:
        return True
    return False


def analyse(buf, base, prov=None, off0=0):
    n = len(buf)
    hw = n // 2
    pools, plaus = 0, 0
    seen = set()
    for p in range(0, n - 1, 2):
        w = buf[p] | (buf[p + 1] << 8)
        if (w & 0xF000) == 0xD000:
            a = ((p + 4) & ~3) + (w & 0xFF) * 4
            if 0 <= a < n - 3 and a not in seen:
                seen.add(a)
                v = struct.unpack_from('<I', buf, a)[0]
                pools += 1
                plaus += plausible(v)
    cls = collections.Counter()
    bad = 0
    p = 0
    while p < n - 1:
        ins = list(MD.disasm(buf[p:], base + p, 1))
        if ins:
            cls[ins[0].mnemonic.split('.')[0].split('/')[0]] += 1
            p += ins[0].size
        else:
            bad += 1
            p += 2
    lit = prov.litfrac(off0, off0 + n) * 100 if prov else float('nan')
    ctl = sum(cls[k] for k in ('bsr', 'bra', 'jsr', 'jmp', 'rts', 'bt', 'bf'))
    return dict(hw=hw, pools=pools, plaus=plaus,
                plauspct=100.0 * plaus / pools if pools else float('nan'),
                badpct=100.0 * bad / hw, lit=lit,
                ctl=100.0 * ctl / hw,
                bsr=cls['bsr'], bra=cls['bra'], jsr=cls['jsr'], rts=cls['rts'],
                movl=cls['mov'])


def row(label, d):
    print(f"{label:<40} {d['hw']:>6} {d['lit']:>7.1f} {d['pools']:>6} "
          f"{d['plauspct']:>8.1f} {d['badpct']:>7.1f} {d['ctl']:>7.1f}"
          f" {d['bsr']:>5} {d['jsr']:>4} {d['rts']:>4}")


def header():
    print(f"{'region':<40} {'halfwd':>6} {'lit%':>7} {'pools':>6} "
          f"{'plaus%':>8} {'undec%':>7} {'flow%':>7} {'bsr':>5} {'jsr':>4} {'rts':>4}")
    print('-' * 108)


if __name__ == '__main__':
    P = Prov()
    if len(sys.argv) >= 3:
        a, b = int(sys.argv[1], 0), int(sys.argv[2], 0)
        if a >= BASE: a -= BASE
        if b >= BASE: b -= BASE
        header()
        row(sys.argv[3] if len(sys.argv) > 3 else f'{BASE+a:#x}',
            analyse(P.img[a:b], BASE + a, P, a))
        sys.exit(0)

    stub = open(os.path.join(os.path.dirname(__file__), '..', 'work', 'MAIN.img'), 'rb').read()
    header()
    row('GROUND TRUTH boot stub 0x0-0xc40',
        analyse(stub[0:0xC40], 0xA0000000))
    for lo, hi, lbl in (
        (0xE7EB80, 0xE7EF00, 'BEU zero-init fn (high literal)'),
        (0xE7C000, 0xE7E800, 'band 0xE7C000-0xE7E800 (high lit)'),
        (0xE7F000, 0xE7FE00, 'DISPATCHER zone 0xE7F000-0xE7FE00'),
        (0xE79400, 0xE79C00, 'BBLCR0 referrer zone 0xE79400+'),
        (0xE7B000, 0xE7BC00, 'band 0xE7B000-0xE7BC00 (low lit)'),
    ):
        row(lbl, analyse(P.img[lo:hi], BASE + lo, P, lo))
    # negative control: a region of known non-code (the palette tables)
    row('CONTROL palette data 0xCC2A00-0xCC5600',
        analyse(P.img[0xCC2A00:0xCC5600], BASE + 0xCC2A00, P, 0xCC2A00))
    print("\nchance notes: a random halfword is bsr/bra with p=1/16 (0xB000 block),")
    print("              bt/bf with p=2/256, jsr @Rn with p=16/65536.")
