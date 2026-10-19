#!/usr/bin/env python3
"""provmap.py -- overlay LZSS provenance on a decoded region.

A decoded byte is only trustworthy if the encoder stored it as a LITERAL:
literals decode to themselves under ANY match semantics, match bytes do not
(CODEC.md: the match semantics are proven wrong -- "ANUAL" <- "MANUAL").

So before believing any disassembly of a compressed region, ask what fraction
of its instruction halfwords are literal-derived. This prints that.

    provmap.py summary  START END [STEP]     literal % per STEP-byte window
    provmap.py dis      START END            disassembly annotated L/M per halfword
    provmap.py bytes    START END            hex dump annotated per byte

Addresses are block1 offsets (add 0x08000000 for the load address).
"""
import mmap, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, '..', 'work')
BIN = os.path.join(WORK, 'OKU.blk01.bin')
PROV = os.path.join(WORK, 'OKU.blk01.prov')
REC = 6                                   # kind u8, hops u8, root u32
BASE = 0x08000000


class Prov:
    def __init__(self, b=BIN, p=PROV):
        self.img = open(b, 'rb').read()
        f = open(p, 'rb')
        self.mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

    def kind(self, o):
        return self.mm[o * REC]

    def hops(self, o):
        return self.mm[o * REC + 1]

    def root(self, o):
        return struct.unpack_from('<I', self.mm, o * REC + 2)[0]

    def litfrac(self, a, b):
        n = b - a
        if n <= 0:
            return 0.0
        return sum(1 for o in range(a, b) if self.mm[o * REC] == 0) / n


def cmd_summary(P, a, b, step):
    print(f"# literal fraction, block1 {a:#x}-{b:#x} (load {BASE+a:#x}-{BASE+b:#x})")
    print(f"{'offset':>10} {'addr':>10}  lit%   bar")
    tot = 0
    for s in range(a, b, step):
        e = min(s + step, b)
        f = P.litfrac(s, e)
        tot += f * (e - s)
        bar = '#' * int(f * 40)
        print(f"{s:#10x} {BASE+s:#10x}  {f*100:5.1f}  {bar}")
    print(f"\noverall literal: {tot/(b-a)*100:.1f}%")


def cmd_bytes(P, a, b):
    for s in range(a, b, 16):
        row = P.img[s:s + 16]
        marks = ''.join('.' if P.kind(o) == 0 else 'M' for o in range(s, min(s + 16, b)))
        print(f"{BASE+s:08x}  {row.hex(' ')}  |{marks}|")


def cmd_dis(P, a, b):
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_SH,
                     capstone.CS_MODE_SH4 | capstone.CS_MODE_LITTLE_ENDIAN)
    img = P.img
    p = a
    while p < b:
        insns = list(md.disasm(img[p:b], BASE + p, 1))
        if insns:
            i = insns[0]
            mn, ops, sz = i.mnemonic, i.op_str, i.size
        else:
            mn, ops, sz = '.word', '%#06x' % struct.unpack_from('<H', img, p)[0], 2
        tag = ''.join('L' if P.kind(o) == 0 else 'm' for o in range(p, p + sz))
        note = ''
        # resolve PC-relative literal pool loads
        if mn in ('mov.l', 'mov.w') and 'pc' not in ops and ops.startswith('0x'):
            pass
        if mn.startswith('mov.') and '(' not in ops and ops.startswith('0x'):
            try:
                tgt = int(ops.split(',')[0], 16) - BASE
                if mn == 'mov.l' and 0 <= tgt < len(img) - 3:
                    v = struct.unpack_from('<I', img, tgt)[0]
                    lt = ''.join('L' if P.kind(o) == 0 else 'm' for o in range(tgt, tgt + 4))
                    note = f'   ; = {v:#010x} [pool {lt}]'
                elif mn == 'mov.w' and 0 <= tgt < len(img) - 1:
                    v = struct.unpack_from('<H', img, tgt)[0]
                    lt = ''.join('L' if P.kind(o) == 0 else 'm' for o in range(tgt, tgt + 2))
                    note = f'   ; = {v:#06x} [pool {lt}]'
            except ValueError:
                pass
        print(f'{BASE+p:08x}  {tag:<4} {img[p:p+sz].hex():<8} {mn:<10} {ops}{note}')
        p += sz


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    a, b = int(sys.argv[2], 0), int(sys.argv[3], 0)
    if a >= BASE:
        a -= BASE
    if b >= BASE:
        b -= BASE
    P = Prov()
    if cmd == 'summary':
        cmd_summary(P, a, b, int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x100)
    elif cmd == 'dis':
        cmd_dis(P, a, b)
    elif cmd == 'bytes':
        cmd_bytes(P, a, b)
    else:
        sys.exit(__doc__)
