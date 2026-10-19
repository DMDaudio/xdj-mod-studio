#!/usr/bin/env python3
"""panl_keymap.py -- map PANL payload bits to key-bitmap bits to named events.

    .venv/bin/python3 tools/panl_keymap.py

Joins two static readings of block1:
  1. the PANL parser 0xD51A64: `tst #mask` on a payload byte, then
     `or #bit` into key byte N of 0x0B6C7A28
  2. the ctsh KY injector 0x8B2ABD8: event code -> (key byte, bit)
The parser tracker is a pattern match over straight-line code, so an entry
whose source it cannot follow prints as payload[?]. Level S only.
"""
import os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shfpu, shdis

HERE = os.path.dirname(os.path.abspath(__file__))
d = open(os.path.join(HERE, '..', 'cache', 'blk1.bin'), 'rb').read()


def parser_map():
    src, r0imm, pending, out = {}, None, None, []
    for l in shfpu.listing(d, 0x08D51A64, 0xCA0):
        p = l.split(None, 3)
        if len(p) < 3:
            continue
        a, ins = p[0], p[2]
        ops = p[3].split(';')[0].strip() if len(p) > 3 else ''
        m = re.match(r'@\((\d+),r1\),r0', ops)
        if ins == 'mov.b' and m:
            src[0] = int(m.group(1)); r0imm = None; continue
        m = re.match(r'@\(r0,r1\),r(\d+)', ops)
        if ins == 'mov.b' and m and r0imm is not None:
            src[int(m.group(1))] = r0imm; continue
        m = re.match(r'r(\d+),r(\d+)$', ops)
        if ins == 'mov' and m:
            s, dn = int(m.group(1)), int(m.group(2))
            if s in src: src[dn] = src[s]
            elif dn in src: del src[dn]
            if dn == 0: r0imm = None
            continue
        m = re.match(r'#(-?\d+),r0$', ops)
        if ins == 'mov' and m:
            r0imm = int(m.group(1)); continue
        m = re.match(r'#(\d+),r0$', ops)
        if ins == 'tst' and m:
            pending = (src.get(0), int(m.group(1)), a); continue
        if ins == 'or' and m and pending:
            out.append([pending[0], pending[1], None, int(m.group(1)), pending[2]]); continue
        m = re.match(r'r(\d+),@\(r0,r13\)', ops)
        if ins == 'mov.b' and m and out and out[-1][2] is None and r0imm is not None:
            out[-1][2] = r0imm; pending = None
    return out


def ky_map():
    names = {}
    for i in range(62):
        code, np_ = struct.unpack_from('<II', d, 0x6DD7C + 8 * i)
        names[code] = d[np_ - 0xA8000000:np_ - 0xA8000000 + 40].split(b'\0')[0].decode()
    res = {}
    for idx in range(91):
        code = idx + 2
        tgt = 0xB2AC00 + struct.unpack_from('<h', d, 0xB2AC14 + 2 * idx)[0]
        if tgt == 0xB2B300:
            continue
        byte = bit = None
        for l in shdis.dis(d, 0x08000000, tgt, 0x1C):
            m = re.search(r'mov\s+#(\d+),r0', l)
            if m and byte is None: byte = int(m.group(1))
            m = re.search(r'\bor\s+#(\d+),r0', l)
            if m and bit is None: bit = int(m.group(1))
        res[(byte, bit)] = f'{names.get(code, "?")}(0x{code:02x})'
    return res


if __name__ == '__main__':
    ky = ky_map()
    for s, msk, kb, bit, a in parser_map():
        nm = ky.get((kb, bit), '') if kb is not None else ''
        sb = f'payload[{s}]' if s is not None else 'payload[?]'
        print(f'{a}  {sb:12} {msk:#04x} -> key byte {kb} bit {bit:#04x}  {nm}')
