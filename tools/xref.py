#!/usr/bin/env python3
"""xref.py -- find references to an address in the decompressed image, and
disassemble around them.

Block1 loads at 0xA8000000, so a decoded offset X is addressed as 0xA8000000+X.
SH-4 has no absolute-address operand, so pointers live in literal pools and are
loaded with mov.l @(disp,PC),Rn -- meaning a reference is a 4-byte little-endian
word equal to the address, and the code using it is a short distance ABOVE it.
"""
import os, struct, sys
import numpy as np
import capstone

# Block1 is loaded to SDRAM and reached through two SH-4 address windows:
#   0xA8000000  P2, uncached  -- used for DATA pointers (strings, tables)
#   0x08000000  P0, cached    -- used for CODE pointers (function addresses)
# Both map the same bytes. Ignoring the cached view hides ~143,500 of the
# ~153,000 pointers in the image, i.e. essentially the whole call graph.
BASE = 0xA8000000
CBASE = 0x08000000
BASES = (0xA8000000, 0x08000000)


def to_off(v, n):
    """Pointer -> image offset, or None if it is not an image pointer."""
    for b in BASES:
        if b <= v < b + n:
            return v - b
    return None


def to_ptr(off, cached=False):
    return (CBASE if cached else BASE) + off
HERE = os.path.dirname(os.path.abspath(__file__))


def load(path=None):
    p = path or os.path.join(HERE, '..', 'cache', 'blk1.bin')
    return open(p, 'rb').read()


def refs(d, off, bases=BASES):
    """-> [(pool offset, which base)] for every 4-aligned word pointing at off."""
    out = []
    for b in bases:
        tgt = struct.pack('<I', b + off)
        i = 0
        while True:
            i = d.find(tgt, i)
            if i < 0:
                break
            if i % 4 == 0:
                out.append((i, b))
            i += 1
    return sorted(out)


_md = capstone.Cs(capstone.CS_ARCH_SH, capstone.CS_MODE_SH4A | capstone.CS_MODE_LITTLE_ENDIAN)


def dis(d, a, n=24, mark=None):
    lines = []
    for k in range(n):
        p = a + k * 2
        if p + 2 > len(d):
            break
        got = False
        for ins in _md.disasm(d[p:p + 2], p):
            s = f"  {p:#09x}  {ins.bytes.hex():4} {ins.mnemonic:9} {ins.op_str}"
            got = True
        if not got:
            s = f"  {p:#09x}  {d[p:p+2].hex():4} .word"
        if mark is not None and p == mark:
            s += "        <<<"
        lines.append(s)
    return '\n'.join(lines)


def pool_users(d, poolo, back=0x400):
    """A literal at poolo is loaded by mov.l @(disp,PC) from below it.
    Return plausible loader sites: any mov.l with a PC-relative target == poolo."""
    hits = []
    lo = max(0, poolo - back)
    for p in range(lo, poolo, 2):
        w = struct.unpack_from('<H', d, p)[0]
        if (w & 0xF000) != 0xD000:          # mov.l @(disp,PC),Rn
            continue
        disp = w & 0xFF
        tgt = ((p + 4) & ~3) + disp * 4
        if tgt == poolo:
            hits.append((p, (w >> 8) & 0x0F))
    return hits


def bsr_callers(d, target, lo=None, hi=None):
    """Find `bsr disp12` sites whose target is `target`.

    SH-4: 1011dddddddddddd, target = PC + 4 + sign_extend(disp) * 2.
    Functions in the same translation unit are called this way, so they carry
    no literal-pool pointer and are invisible to refs().
    """
    out = []
    lo = 0 if lo is None else lo
    hi = len(d) - 1 if hi is None else hi
    # a bsr can reach +/-4KB, so only scan that window
    lo = max(lo, target - 0x1010)
    hi = min(hi, target + 0x1010)
    for p in range(lo & ~1, hi, 2):
        w = struct.unpack_from('<H', d, p)[0]
        if (w & 0xF000) != 0xB000:
            continue
        disp = w & 0x0FFF
        if disp & 0x800:
            disp -= 0x1000
        if p + 4 + disp * 2 == target:
            out.append(p)
    return out


def func_start(d, addr, back=0x800):
    """Walk back to the nearest plausible function prologue.

    SH-4 gcc prologues save callee-saved regs then pr:
        mov.l rN,@-r15   (0x2NF6)   sts.l pr,@-r15 (0x4F22)
    The first instruction of the run is the entry point.
    """
    best = None
    for p in range((addr - back) & ~1, addr, 2):
        w = struct.unpack_from('<H', d, p)[0]
        if w == 0x4F22 or (w & 0xFF0F) == 0x2F06:      # sts.l pr / mov.l Rm,@-r15
            q = p
            while q - 2 >= 0:
                v = struct.unpack_from('<H', d, q - 2)[0]
                if (v & 0xFF0F) == 0x2F06:
                    q -= 2
                else:
                    break
            best = q
    return best


def callers(d, target):
    """Every call site: literal-pool jsr plus PC-relative bsr."""
    out = []
    for pr, b in refs(d, target):
        for site, reg in pool_users(d, pr):
            out.append((site, f'jsr via r{reg} (pool {pr:#x})'))
    for p in bsr_callers(d, target):
        out.append((p, 'bsr'))
    return sorted(out)


if __name__ == '__main__':
    d = load()
    off = int(sys.argv[1], 0)
    print(f"target {off:#x}  (address {BASE+off:#010x})\n")
    for r, b in refs(d, off):
        print(f"literal pool word at {r:#09x}   (via {'cached 0x08' if b == CBASE else 'uncached 0xA8'})")
        for site, reg in pool_users(d, r):
            print(f"  loaded into r{reg} at {site:#09x}")
            print(dis(d, max(0, site - 20), 22, mark=site))
            print()
