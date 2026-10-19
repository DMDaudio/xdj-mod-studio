#!/usr/bin/env python3
"""litstrings.py -- extract strings, keeping only the ones we can trust.

A string in a compressed block is only real if the encoder stored it as
LITERALS (those decode correctly under any match semantics). Match-derived
"strings" are hallucinated by our wrong codec, and match-derived *prefixes* are
why the decode shows `ANUAL` for `MANUAL`.

    litstrings.py FILE.bin FILE.prov [LO HI] [--min N] [--all]

--all also prints partly-match strings, marked, so you can see the truncation.
"""
import os, sys

REC = 6
PRINT = set(range(0x20, 0x7F))


def main():
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    show_all = '--all' in sys.argv
    mn = 4
    if '--min' in sys.argv:
        mn = int(sys.argv[sys.argv.index('--min') + 1])
    img = open(a[0], 'rb').read()
    prov = open(a[1], 'rb').read()
    lo = int(a[2], 0) if len(a) > 2 else 0
    hi = int(a[3], 0) if len(a) > 3 else len(img)
    kind = prov[0::REC]

    o = lo
    while o < hi:
        if img[o] in PRINT:
            s = o
            while o < hi and img[o] in PRINT:
                o += 1
            if o - s >= mn:
                nl = sum(1 for x in range(s, o) if kind[x] == 0)
                frac = nl / (o - s)
                txt = img[s:o].decode('latin1')
                if frac == 1.0:
                    print(f'{s:#09x}  LITERAL      {txt}')
                elif show_all:
                    tg = ''.join('L' if kind[x] == 0 else '.' for x in range(s, o))
                    print(f'{s:#09x}  {frac*100:3.0f}% lit     {txt}')
                    print(f'{"":9}               {tg}')
        else:
            o += 1


if __name__ == '__main__':
    main()
