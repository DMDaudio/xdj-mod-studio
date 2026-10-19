#!/usr/bin/env python3
"""shfpu.py -- SH-4 disassembly with the FPU opcodes capstone leaves as .word.

    .venv/bin/python3 tools/shfpu.py 0x08BF0E60 0xE0

Wraps shdis.dis() and decodes 0xFxxx FPU instructions plus the
lds/sts FPUL/FPSCR forms. PR (double-precision) mode is tracked from the
common `sts fpscr,Rx; and mask,Rx; lds Rx,fpscr` sequence where the mask is a
literal: 0xFFE7FFFF clears PR+SZ (single), an `or` of 0x00080000 sets PR.
When PR=1, register operands print as DRn. This is a reading aid, not proof:
where PR cannot be followed it prints FR and marks the line with '?pr'.
"""
import os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shdis

BASE = 0x08000000


def fpu(w, pr):
    n, m, lo = (w >> 8) & 15, (w >> 4) & 15, w & 15
    R = (lambda i: f'dr{i}') if pr else (lambda i: f'fr{i}')
    if (w & 0xF000) == 0xF000:
        ops = {0: 'fadd', 1: 'fsub', 2: 'fmul', 3: 'fdiv'}
        if lo in ops:
            return f'{ops[lo]:<10} {R(m)},{R(n)}'
        if lo == 4: return f'fcmp/eq    {R(m)},{R(n)}'
        if lo == 5: return f'fcmp/gt    {R(m)},{R(n)}'
        if lo == 6: return f'fmov       @(r0,r{m}),fr{n}'
        if lo == 7: return f'fmov       fr{m},@(r0,r{n})'
        if lo == 8: return f'fmov       @r{m},fr{n}'
        if lo == 9: return f'fmov       @r{m}+,fr{n}'
        if lo == 0xA: return f'fmov       fr{m},@r{n}'
        if lo == 0xB: return f'fmov       fr{m},@-r{n}'
        if lo == 0xC: return f'fmov       fr{m},fr{n}'
        if lo == 0xE: return f'fmac       fr0,fr{m},fr{n}'
        if lo == 0xD:
            sub = {0: f'fsts       fpul,fr{n}', 1: f'flds       fr{n},fpul',
                   2: f'float      fpul,{R(n)}', 3: f'ftrc       {R(n)},fpul',
                   4: f'fneg       {R(n)}', 5: f'fabs       {R(n)}',
                   6: f'fsqrt      {R(n)}', 7: f'fsrra      fr{n}',
                   8: f'fldi0      fr{n}', 9: f'fldi1      fr{n}',
                   0xA: f'fcnvsd     fpul,dr{n}', 0xB: f'fcnvds     dr{n},fpul'}
            if m in sub: return sub[m]
            if w == 0xF3FD: return 'fschg'
            if w == 0xFBFD: return 'frchg'
    tail = w & 0xF0FF
    if tail == 0x405A: return f'lds        r{n},fpul'
    if tail == 0x005A: return f'sts        fpul,r{n}'
    if tail == 0x406A: return f'lds        r{n},fpscr'
    if tail == 0x006A: return f'sts        fpscr,r{n}'
    if tail == 0x4056: return f'lds.l      @r{n}+,fpul'
    if tail == 0x4066: return f'lds.l      @r{n}+,fpscr'
    if tail == 0x4052: return f'sts.l      fpul,@-r{n}'
    if tail == 0x4062: return f'sts.l      fpscr,@-r{n}'
    return None


def listing(d, start, n):
    pr = False
    known = True
    lit = {}                       # reg -> literal value loaded by mov.l
    for line in shdis.dis(d, BASE, start - BASE, n):
        a = int(line.split()[0], 16)
        w = struct.unpack_from('<H', d, a - BASE)[0]
        mm = re.search(r'mov\.l\s+0x[0-9a-f]+,r(\d+)\s+; = (0x[0-9a-f]+)', line)
        if mm:
            lit[int(mm.group(1))] = int(mm.group(2), 16)
        rn = (w >> 8) & 15
        if (w & 0xF000) == 0xE000:                      # mov #imm,Rn
            lit[rn] = struct.unpack('b', bytes([w & 0xFF]))[0] & 0xFFFFFFFF
        elif (w & 0xF0FF) == 0x4018 and rn in lit:      # shll8
            lit[rn] = (lit[rn] << 8) & 0xFFFFFFFF
        elif (w & 0xF0FF) == 0x4028 and rn in lit:      # shll16
            lit[rn] = (lit[rn] << 16) & 0xFFFFFFFF
        # and Rm,Rn with a known mask literal, applied to an fpscr copy
        if (w & 0xF00F) == 0x2009:
            msk = lit.get((w >> 4) & 15)
            if msk == 0xFFE7FFFF:
                pr, known = False, True
        if (w & 0xF00F) == 0x200B:
            v = lit.get((w >> 4) & 15)
            if v is not None and v & 0x00080000:
                pr, known = True, True
        f = fpu(w, pr)
        if f is not None and ('.word' in line or (w & 0xF000) == 0xF000):
            mark = '' if known else '   ?pr'
            line = f'{a:08x}  {line.split()[1]:<8} {f}{mark}'
        yield line


if __name__ == '__main__':
    d = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cache', 'blk1.bin'), 'rb').read()
    s = int(sys.argv[1], 0)
    s = s if s >= BASE else s + BASE
    for l in listing(d, s, int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x100):
        print(l)
