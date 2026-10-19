#!/usr/bin/env python3
"""oracle.py -- score a candidate decompression against SH7724 hardware truth.

THE PROBLEM IT SOLVES
Every earlier codec test was circular: statistics computed from a decode can
only tell you the decode is self-consistent. Worse, LZSS *literals* decode
correctly under ANY match semantics -- so readable strings, correct register
constants and plausible word counts all survive a wrong codec. That is exactly
why "V1 beats V2 on real words" was not evidence.

THE ORACLE
SH-4 loads a 32-bit constant only via `mov.l @(disp,PC),Rn` (opcode 0xDnxx),
which reaches a 4-byte-aligned literal within +1020 bytes. Peripheral register
addresses (BEU 0xFE93xxxx, LCDC 0xFE94xxxx, PFC 0xA405xxxx, ...) are FIXED BY
SILICON -- we know them from the manual, independent of any decode. So:

    in a CORRECT decode, essentially every aligned occurrence of a real
    register address must be the target of some mov.l @(disp,PC).

That coupling is destroyed by a wrong codec (the constant is a literal and
survives; the instruction referencing it is a match and does not).

CALIBRATION on the uncompressed boot stub -- real code, no decode involved:
    30 aligned register constants, 30 mov.l-targeted = 100%
    (chance baseline for that buffer: 13.5%)

Usage:
    oracle.py FILE [FILE...]        # score decoded blocks
    oracle.py --calibrate           # re-run the boot-stub ground truth
"""
import json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGS = os.path.join(HERE, '..', 'docs', 'sh7724_registers.json')


def reg_set():
    return set(json.load(open(REGS)).values())


def movl_targets(buf):
    """Offsets reached by some `mov.l @(disp,PC),Rn`."""
    t = set()
    for p in range(0, len(buf) - 1, 2):
        w = buf[p] | (buf[p + 1] << 8)
        if (w & 0xF000) == 0xD000:
            a = ((p + 4) & ~3) + (w & 0xFF) * 4
            if 0 <= a < len(buf) - 3:
                t.add(a)
    return t


def score(buf, real=None):
    real = real or reg_set()
    ptd = movl_targets(buf)
    consts = [o for o in range(0, len(buf) - 3, 4)
              if struct.unpack_from('<I', buf, o)[0] in real]
    hit = [o for o in consts if o in ptd]
    aligned = len(range(0, len(buf) - 3, 4))
    baseline = len(ptd & set(range(0, len(buf) - 3, 4))) / max(aligned, 1)
    return dict(consts=len(consts), targeted=len(hit),
                pct=len(hit) * 100 // max(len(consts), 1),
                baseline=round(baseline * 100, 1))


def show(label, buf, real):
    s = score(buf, real)
    verdict = ('CORRECT-ish' if s['pct'] >= 70 else
               'WRONG' if s['consts'] >= 20 else 'too few constants')
    print(f"  {label:<34} consts={s['consts']:>4}  targeted={s['targeted']:>4} "
          f"({s['pct']:>3}%)  chance={s['baseline']:>4}%   {verdict}")
    return s


if __name__ == '__main__':
    real = reg_set()
    args = sys.argv[1:]
    if not args or args[0] == '--calibrate':
        img = open(os.path.join(HERE, '..', 'work', 'MAIN.img'), 'rb').read()
        print("calibration (uncompressed, no decode involved):")
        show('boot stub 0x0-0xc40', img[0:0xc40], real)
        if not args:
            print("\nusage: oracle.py FILE [FILE...]")
        sys.exit(0)
    print("scoring:")
    for f in args:
        show(os.path.basename(f), open(f, 'rb').read(), real)
