#!/usr/bin/env python3
"""sh4box.py -- run caller-supplied SH-4 machine code on a FRESH cpu.

No checkpoint, no devices, no pending interrupts: deterministic sandbox
for validating shasm-assembled patchlets. RAM at 0x0B000000 (64 MB);
entry at 0x0B100000; rts (with nop delay) returns to MAGIC and stops.

Usage: sh4box.run_code(code, regs={4:..}, data={addr: bytes}) -> (regs, mem)
"""
import sys
sys.path.insert(0, ".")
from emu.sh4 import SH4, PcHalt

MAGIC = 0x08000118
RAM_BASE = 0x0B000000
RAM_SIZE = 64 * 1024 * 1024
ENTRY = 0x0B100000


class BoxMem:
    def __init__(self):
        self.ram = bytearray(RAM_SIZE)
        self.tick = None

    def _t(self, a):
        if RAM_BASE <= a < RAM_BASE + RAM_SIZE:
            return a - RAM_BASE
        if 0x08000000 <= a < 0x08001000:
            return None  # MAGIC nop-slide: reads 0
        raise IndexError(f"oob {a:#x}")

    def r8(self, a):
        o = self._t(a)
        return 0 if o is None else self.ram[o]

    def r16(self, a):
        return self.r8(a) | (self.r8(a + 1) << 8)

    def r32(self, a):
        return self.r8(a) | (self.r8(a + 1) << 8) | (self.r8(a + 2) << 16) | (self.r8(a + 3) << 24)

    def r64(self, a):
        return self.r32(a) | (self.r32(a + 4) << 32)

    def w8(self, a, v):
        self.ram[self._t(a)] = v & 0xFF

    def w16(self, a, v):
        self.w8(a, v)
        self.w8(a + 1, v >> 8)

    def w32(self, a, v):
        for i in range(4):
            self.w8(a + i, v >> (8 * i))

    def w64(self, a, v):
        self.w32(a, v)
        self.w32(a + 4, v >> 32)

    def take_interrupt(self, imask):
        return None


def run_code(code, regs=None, data=None, limit=100000):
    mem = BoxMem()
    for i, b in enumerate(code):
        mem.w8(ENTRY + i, b)
    for addr, blob in (data or {}).items():
        for i, b in enumerate(blob):
            mem.w8(addr + i, b)
    c = SH4(mem)
    from emu.sh4fpu import FPU
    c.fpu = FPU(c)
    from emu.sh4 import MD, RB, BL, FD
    c.sr = MD | BL | 0xF0
    c.b0 = [0] * 8
    c.b1 = [0] * 8
    c.rhi = [0] * 8
    c.rhi[7] = RAM_BASE + RAM_SIZE - 256  # stack (grows down)
    for r, v in (regs or {}).items():
        if r < 8:
            c.b0[r] = v & 0xFFFFFFFF
        else:
            c.rhi[r - 8] = v & 0xFFFFFFFF
    c.pr = MAGIC
    c.pc = ENTRY

    def tr(pc, op):
        if pc == MAGIC:
            raise PcHalt(pc)
    c.trace = tr
    try:
        res = c.run(limit=limit)
    except PcHalt:
        res = f"halt at {MAGIC:#x}"
    c.trace = None
    return c, mem, res


if __name__ == "__main__":
    sys.path.insert(0, "tools")
    from shasm import assemble
    src = open(sys.argv[1]).read()
    code = assemble(src)
    regs = {}
    for a in sys.argv[2:]:
        r, v = a.split("=")
        regs[int(r)] = int(v, 0)
    c, mem, res = run_code(code, regs)
    print("run:", res)
    print("r0-r7:", [hex(c.gr(i)) for i in range(8)])
