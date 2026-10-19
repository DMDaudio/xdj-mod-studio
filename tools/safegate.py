#!/usr/bin/env python3
"""safegate.py -- which colour bytes can we edit and be sure nothing else moves?

A literal byte patched in the LZSS stream always decodes to itself (that part is
codec-independent). The danger is downstream: it also enters the ring, and later
MATCHES may copy it. Which ones do is decided by the match semantics, and ours
are proven wrong -- so a fan-out figure from ONE decoder is not a bound.

This applies a two-codec agreement gate. For each literal stream byte we count
how many output bytes descend from it under V1 and under Okumura, and require
both counts to be small. "Small" is CALIBRATED on the version-string edit at
0x568 -- the only payload edit that has ever flashed successfully -- whose worst
byte has fan-out 1 under V1 and 3 under Okumura. So the gate is max fan-out <= 3
under both.

*** THIS IS NOT A SAFETY GUARANTEE. *** The deck's decompressor is a third
semantics we do not have. Passing the gate means two wrong models both say the
edit is local; it does not say the right one agrees. Nothing here is a
recommendation to flash the quarantined palette region.

    safegate.py                 audit the palette tables
    safegate.py LO HI           audit an arbitrary V1 output range
"""
import collections, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, '..', 'work')
REC = 6
BASE = 0x08000000

TABLES = [(0xCC2A70, 11, 'amber/brown ramp'), (0xCC2AC0, 19, 'gold + blue ramp'),
          (0xCC2B14, 16, 'blue + teal ramp'), (0xCC5288, 6, 'near-whites'),
          (0xCC52A8, 5, 'light greys'), (0xCC52E0, 4, 'whites'),
          (0xCC5368, 14, 'mid greys'), (0xCC53F8, 14, 'near-blacks'),
          (0xCC54D8, 5, 'very dark')]


def load(prefix):
    img = open(os.path.join(WORK, f'{prefix}.blk01.bin'), 'rb').read()
    prov = open(os.path.join(WORK, f'{prefix}.blk01.prov'), 'rb').read()
    n = len(img)
    kind = prov[0::REC]
    root = struct.unpack(f'<{n}I', b''.join(prov[i * REC + 2:i * REC + 6] for i in range(n))) \
        if False else None
    # faster: pull the u32 column out with a memoryview stride walk
    mv = memoryview(prov)
    root = [0] * n
    for i in range(n):
        o = i * REC + 2
        root[i] = mv[o] | (mv[o + 1] << 8) | (mv[o + 2] << 16) | (mv[o + 3] << 24)
    fan = collections.Counter(root)
    lit_at = {}                       # stream offset -> output offset (literals)
    for i in range(n):
        if kind[i] == 0:
            lit_at[root[i]] = i
    return img, kind, root, fan, lit_at


def main():
    print('decoding provenance for both codecs (this takes a moment) ...',
          file=sys.stderr)
    v_img, v_kind, v_root, v_fan, v_lit = load('V1')
    o_img, o_kind, o_root, o_fan, o_lit = load('OKU')

    ranges = ([(int(sys.argv[1], 0), int(sys.argv[2], 0), 'range')]
              if len(sys.argv) > 2 else
              [(a, a + n * 4, lbl) for a, n, lbl in TABLES])

    K = 3          # calibrated on the version-string edit (see docstring)
    print(f"\n{'V1 output range':<24} {'bytes':>6} {'literal':>8} "
          f"{'fan<=3 V1':>12} {'fan<=3 BOTH':>14}  label")
    print('-' * 92)
    gt = gl = gb = gn = 0
    for a, b, lbl in ranges:
        nb = b - a
        lits = [o for o in range(a, b) if v_kind[o] == 0]
        s1 = [o for o in lits if v_fan[v_root[o]] <= K]
        s2 = [o for o in s1 if o_fan.get(v_root[o], 0) <= K]
        gt += nb; gl += len(lits); gn += len(s1); gb += len(s2)
        print(f'{BASE+a:#010x}-{BASE+b:#08x} {nb:>6} {len(lits):>8} '
              f'{len(s1):>12} {len(s2):>14}  {lbl}')
    print('-' * 92)
    print(f"{'TOTAL':<24} {gt:>6} {gl:>8} {gn:>12} {gb:>14}")
    print(f"\n{gb} of {gt} palette bytes ({100.0*gb/gt:.1f}%) are patchable with "
          f"fan-out <= 3 under both codecs.")
    # the control: the version string, the only edit ever flashed successfully
    vs = [o for o in range(0x568, 0x56C) if v_kind[o] == 0]
    ok = [o for o in vs if v_fan[v_root[o]] <= K and o_fan.get(v_root[o], 0) <= K]
    print(f"control -- version string 0x568: {len(vs)}/4 literal, "
          f"{len(ok)}/4 pass the gate  (this edit flashed successfully twice)")


if __name__ == '__main__':
    main()
