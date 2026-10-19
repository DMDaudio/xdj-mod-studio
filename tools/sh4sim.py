"""sh4sim.py -- execute the deck's OWN decompressor over a candidate stream.

Why this exists
---------------
Everything else in the toolchain rests on one assumption: that
`tools/lzss_boot.py` is a faithful transcription of the SH-4 routine at
MAIN.img 0x0900-0x09C2. `decode(encode(x)) == x` proves that decoder is
self-consistent. It does NOT prove it matches the machine code the deck runs.

That assumption is exactly the class of error that bricked a deck: a codec
believed correct, a payload built on it, and a checksum that told the boot ROM
to trust the result.

So this removes the assumption. It interprets the real instructions, out of the
real firmware image, over the real candidate stream, and compares the bytes
with what our Python decoder produced. If they ever disagree, the Python
decoder is wrong and nothing built with it should be flashed.

Scope: only the ~21 mnemonics that routine uses are implemented. Anything else
raises -- this is a verifier, not a general emulator, and silently guessing at
an unimplemented instruction would defeat the point.
"""
import struct

RING = 0x0FFFD340
IN_BASE = 0x30000000
OUT_BASE = 0x40000000
ENTRY = 0x900


class Mem:
    def __init__(self, regions, watch=None):
        self.r = sorted(regions, key=lambda x: x[0])
        self.watch = watch          # base address to watermark writes against
        self.high = 0

    def _find(self, a):
        for base, buf in self.r:
            if base <= a < base + len(buf):
                return buf, a - base
        raise MemoryError(f'unmapped address {a:#010x}')

    def r8(self, a):
        b, o = self._find(a); return b[o]

    def w8(self, a, v):
        b, o = self._find(a); b[o] = v & 0xFF
        if self.watch is not None and a >= self.watch:
            d = a - self.watch + 1
            if d > self.high:
                self.high = d

    def r32(self, a):
        b, o = self._find(a); return struct.unpack_from('<I', b, o)[0]


def s8(v):
    return v - 0x100 if v & 0x80 else v


class SH4:
    def __init__(self, mem):
        self.m = mem
        self.r = [0] * 16
        self.pr = 0
        self.t = 0
        self.pc = 0
        self.steps = 0

    def run(self, entry, r4, r5, r6, limit=2_000_000_000):
        self.r[4], self.r[5], self.r[6] = r4, r5, r6
        self.r[15] = 0x0FFF0000          # stack, inside the mapped scratch page
        self.pc = entry
        self.pr = 0xDEAD0000             # sentinel: returning here ends the run
        while True:
            if self.steps > limit:
                raise RuntimeError('instruction limit exceeded')
            op = self.m.r32(self.pc & ~3)
            op = (op >> (16 * ((self.pc >> 1) & 1))) & 0xFFFF
            nxt = self.pc + 2
            branch = self.step(op)
            self.steps += 1
            if branch is not None:
                tgt, delay = branch
                if delay:                       # execute the delay slot first
                    d = self.m.r32(nxt & ~3)
                    d = (d >> (16 * ((nxt >> 1) & 1))) & 0xFFFF
                    self.pc = nxt
                    if self.step(d) is not None:
                        raise RuntimeError('branch in delay slot')
                    self.steps += 1
                if tgt == 0xDEAD0000:
                    return
                self.pc = tgt
            else:
                self.pc = nxt

    def step(self, op):
        r = self.r
        n = (op >> 8) & 0xF
        m = (op >> 4) & 0xF
        d8 = op & 0xFF
        if op == 0x0009:                                    # nop
            return None
        if op == 0x000B:                                    # rts
            return (self.pr, True)
        if op == 0x4F22:                                    # sts.l pr,@-r15
            r[15] = (r[15] - 4) & 0xFFFFFFFF
            self.m.w8(r[15], self.pr & 0xFF)
            self.m.w8(r[15] + 1, (self.pr >> 8) & 0xFF)
            self.m.w8(r[15] + 2, (self.pr >> 16) & 0xFF)
            self.m.w8(r[15] + 3, (self.pr >> 24) & 0xFF)
            return None
        if op == 0x4F26:                                    # lds.l @r15+,pr
            self.pr = self.m.r32(r[15]); r[15] = (r[15] + 4) & 0xFFFFFFFF
            return None
        top = op >> 12
        if top == 0x2 and (op & 0xF) == 0x6:                # mov.l Rm,@-Rn
            r[n] = (r[n] - 4) & 0xFFFFFFFF
            v = r[m]
            for i in range(4):
                self.m.w8(r[n] + i, (v >> (8 * i)) & 0xFF)
            return None
        if top == 0x6 and (op & 0xF) == 0x6:                # mov.l @Rm+,Rn
            r[n] = self.m.r32(r[m]); r[m] = (r[m] + 4) & 0xFFFFFFFF
            return None
        if top == 0xD:                                      # mov.l @(disp,PC),Rn
            r[n] = self.m.r32(((self.pc + 4) & ~3) + d8 * 4)
            return None
        if top == 0xE:                                      # mov #imm,Rn
            r[n] = s8(d8) & 0xFFFFFFFF
            return None
        if top == 0x7:                                      # add #imm,Rn
            r[n] = (r[n] + s8(d8)) & 0xFFFFFFFF
            return None
        if top == 0x6 and (op & 0xF) == 0x3:                # mov Rm,Rn
            r[n] = r[m]; return None
        if top == 0x6 and (op & 0xF) == 0x4:                # mov.b @Rm+,Rn
            r[n] = s8(self.m.r8(r[m])) & 0xFFFFFFFF
            r[m] = (r[m] + 1) & 0xFFFFFFFF
            return None
        if top == 0x6 and (op & 0xF) == 0xC:                # extu.b Rm,Rn
            r[n] = r[m] & 0xFF; return None
        if top == 0x2 and (op & 0xF) == 0x0:                # mov.b Rm,@Rn
            self.m.w8(r[n], r[m] & 0xFF); return None
        if top == 0x0 and (op & 0xF) == 0xC:                # mov.b @(R0,Rm),Rn
            r[n] = s8(self.m.r8((r[0] + r[m]) & 0xFFFFFFFF)) & 0xFFFFFFFF
            return None
        if top == 0x0 and (op & 0xF) == 0x4:                # mov.b Rm,@(R0,Rn)
            self.m.w8((r[0] + r[n]) & 0xFFFFFFFF, r[m] & 0xFF); return None
        if top == 0x8:
            sub = (op >> 8) & 0xF
            if sub == 0x0:                                  # mov.b R0,@(disp,Rn)
                self.m.w8((r[m] + (op & 0xF)) & 0xFFFFFFFF, r[0] & 0xFF); return None
            if sub == 0x4:                                  # mov.b @(disp,Rm),R0
                r[0] = s8(self.m.r8((r[m] + (op & 0xF)) & 0xFFFFFFFF)) & 0xFFFFFFFF
                return None
            disp = s8(d8) * 2 + 4
            if sub == 0x9: return (self.pc + disp, False) if self.t else None      # bt
            if sub == 0xB: return None if self.t else (self.pc + disp, False)      # bf
            if sub == 0xD: return (self.pc + disp, True) if self.t else None       # bt/s
            if sub == 0xF: return None if self.t else (self.pc + disp, True)       # bf/s
            raise NotImplementedError(f'op {op:#06x}')
        if top == 0xA:                                      # bra
            disp = op & 0xFFF
            if disp & 0x800: disp -= 0x1000
            return (self.pc + 4 + disp * 2, True)
        if top == 0x4:
            lo = op & 0xFF
            if lo == 0x18: r[n] = (r[n] << 8) & 0xFFFFFFFF; return None      # shll8
            if lo == 0x08: r[n] = (r[n] << 2) & 0xFFFFFFFF; return None      # shll2
            if lo == 0x01: r[n] >>= 1; return None                            # shlr
            if lo == 0x10:                                                    # dt
                r[n] = (r[n] - 1) & 0xFFFFFFFF; self.t = int(r[n] == 0); return None
            if lo == 0x11: self.t = int(not (r[n] & 0x80000000)); return None  # cmp/pz
            raise NotImplementedError(f'op {op:#06x}')
        if top == 0x3 and (op & 0xF) == 0x7:                # cmp/gt Rm,Rn
            a = r[n] - (1 << 32) if r[n] & 0x80000000 else r[n]
            b = r[m] - (1 << 32) if r[m] & 0x80000000 else r[m]
            self.t = int(a > b); return None
        if top == 0x3 and (op & 0xF) == 0xC:                # add Rm,Rn
            r[n] = (r[n] + r[m]) & 0xFFFFFFFF; return None
        if top == 0x2 and (op & 0xF) == 0x9:                # and Rm,Rn
            r[n] &= r[m]; return None
        if top == 0x2 and (op & 0xF) == 0xB:                # or Rm,Rn
            r[n] |= r[m]; return None
        if top == 0x2 and (op & 0xF) == 0x8:                # tst Rm,Rn
            self.t = int((r[n] & r[m]) == 0); return None
        if op >> 8 == 0xC9:                                 # and #imm,R0
            r[0] &= d8; return None
        if op >> 8 == 0xC8:                                 # tst #imm,R0
            self.t = int((r[0] & d8) == 0); return None
        raise NotImplementedError(f'unimplemented op {op:#06x} at {self.pc:#x}')


def decompress(main_img, stream, out_len=None):
    """Run the firmware's own decompressor over `stream`.

    The routine runs until its input is exhausted, so the output buffer is
    sized for the worst case: every 2-byte match token can emit 18 bytes.
    """
    code = bytearray(main_img[:0xC40])
    ring = bytearray(0x1011)
    src = bytearray(stream)
    cap = len(src) * 9 + 64
    dst = bytearray(cap)
    scratch = bytearray(0x10000)
    mem = Mem([(0x0, code), (RING, ring), (IN_BASE, src),
               (OUT_BASE, dst), (0x0FFF0000 - 0x8000, scratch)], watch=OUT_BASE)
    cpu = SH4(mem)
    cpu.run(ENTRY, IN_BASE, OUT_BASE, len(src))
    # r13 is the output cursor DURING the run, but the epilogue pops it, so read
    # the length from the memory write watermark instead of the register.
    produced = mem.high
    n = produced if out_len is None else min(out_len, produced)
    return bytes(dst[:n]), cpu.steps
