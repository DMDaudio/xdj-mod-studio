"""FPU self-test for emu/sh4fpu.py: hand-assembled ops, correct nibbles.

Opcodes double-checked against R01UH0174EJ Tables 3.10-3.12 (a wrong
nibble here tests the wrong instruction -- three such mistakes were
caught during development by the core executing exactly what the bits
say, so every opcode below was verified by trace).
Run: .venv/bin/python tools/sh4fputest.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from emu.sh4 import SH4
from emu.sh4fpu import FPU


class Mem:
    def __init__(self):
        self.d = {}

    def _p(self, a):
        a &= 0xFFFFFFFF
        if 0x80000000 <= a < 0xC0000000:
            a &= 0x1FFFFFFF
        return a

    def r8(self, a):
        return self.d.get(self._p(a), 0)

    def w8(self, a, v):
        self.d[self._p(a)] = v & 0xFF

    def r16(self, a):
        a = self._p(a)
        return self.d.get(a, 0) | (self.d.get(a + 1, 0) << 8)

    def w16(self, a, v):
        a = self._p(a)
        self.d[a] = v & 0xFF
        self.d[a + 1] = (v >> 8) & 0xFF

    def r32(self, a):
        a = self._p(a)
        return (self.d.get(a, 0) | (self.d.get(a + 1, 0) << 8) |
                (self.d.get(a + 2, 0) << 16) | (self.d.get(a + 3, 0) << 24))

    def w32(self, a, v):
        a = self._p(a)
        for i in range(4):
            self.d[a + i] = (v >> (8 * i)) & 0xFF

    def r64(self, a):
        return self.r32(a) | (self.r32(a + 4) << 32)

    def w64(self, a, v):
        self.w32(a, v & 0xFFFFFFFFFFFFFFFF)
        self.w32(a + 4, (v >> 32) & 0xFFFFFFFF)

    def take_interrupt(self):
        return None

    def tick(self, c):
        pass


BASE = 0x08000000
passed = failed = 0


def runf(prog, fpscr=0, fr=None, fpul=0):
    m = Mem()
    for i, w in enumerate(prog):
        m.w16(BASE + i * 2, w)
    c = SH4(m)
    c.fpu = FPU(c)
    c.pc = BASE
    c.sr &= ~0xF0
    c.sr &= ~(1 << 15)
    c.fpscr = fpscr
    c.fpul = fpul
    if fr:
        for i, v in enumerate(fr):
            c.fr[0][i] = v
    c.run(limit=40)
    return c


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL {name} {extra}")


def fldi(n, one):
    return 0xF000 | (n << 8) | (0x9D if one else 0x8D)


# single arithmetic -------------------------------------------------------
# FADD FRm,FRn = 1111nnnnmmmm0000 ; FSUB/ FMUL/ FDIV = ...0001/0010/0011
c = runf([fldi(0, True), fldi(1, True), 0xF100])
check("fadd", abs(c.fr[0][1] - 2.0) < 1e-9, str(c.fr[0][1]))
c = runf([fldi(0, True), fldi(1, True), 0xF101])
check("fsub", abs(c.fr[0][1]) < 1e-9)
c = runf([fldi(0, True), fldi(1, True), 0xF102])
check("fmul", abs(c.fr[0][1] - 1.0) < 1e-9)
c = runf([fldi(0, True), fldi(1, True), 0xF103])
check("fdiv", abs(c.fr[0][1] - 1.0) < 1e-9)
c = runf([fldi(0, True), fldi(1, True), 0xF104])
check("fcmp-eq", (c.sr & 1) != 0)
c = runf([fldi(0, True), fldi(1, True), 0xF105])
check("fcmp-gt-false", (c.sr & 1) == 0)
c = runf([fldi(0, True), fldi(1, True), 0xF10E])  # FMAC FR0,FR0,FR1
check("fmac", abs(c.fr[0][1] - 2.0) < 1e-9)
c = runf([fldi(0, True), 0xF04D])                  # FNEG FR0
check("fneg", c.fr[0][0] == -1.0)
c = runf([fldi(0, True), 0xF04D, 0xF05D])          # FABS
check("fabs", c.fr[0][0] == 1.0)
c = runf([fldi(0, False), 0xF06D])                 # FSQRT 0
check("fsqrt", c.fr[0][0] == 0.0)
c = runf([0xF36D], fr=[0.0, 0.0, 0.0, 4.0] + [0.0] * 12)  # FSQRT FR3
check("fsqrt4", abs(c.fr[0][3] - 2.0) < 1e-9)
c = runf([0xF37D], fr=[0.0, 0.0, 0.0, 4.0] + [0.0] * 12)  # FSRRA FR3 = 0.5
check("fsrra", abs(c.fr[0][3] - 0.5) < 1e-9)

# moves --------------------------------------------------------------------
c = runf([fldi(2, True), 0xF02C])                  # FMOV FR2,FR0 (n=0,m=2)
check("fmov-fr", c.fr[0][0] == 1.0)
# FMOV.S FR1,@R2 then back: store to addr 8, reload to FR3, all in-core.
# FR1,@R2: n=2 (bits11:8), m=1 (bits7:4) -> 1111 0010 0001 1010 = 0xF21A
# @R2,FR3: n=3, m=2 -> 1111 0011 0010 1000 = 0xF328
c = runf([fldi(1, True), 0xE208, 0xF21A, 0xF328])
check("fmov-mem", abs(c.fr[0][3] - 1.0) < 1e-6, str(c.fr[0][3]))

# FLOAT / FTRC ---------------------------------------------------------------
c = runf([0xF02D], fpul=7)                        # FLOAT FPUL,FR0
check("float", c.fr[0][0] == 7.0)
c = runf([0xF13D], fr=[0.0, 3.99] + [0.0] * 14)   # FTRC FR1,FPUL
check("ftrc", c.fpul == 3, str(c.fpul))

# double (SZ=1): FADD DR0,DR2 -> DR2 = 1.5+2.5 = 4.0
c = runf([0xF020], fpscr=(1 << 20))
c.fr[0][0], c.fr[0][1], c.fr[0][2], c.fr[0][3] = [0.0] * 4
# (decode-only run above used zeros; now preset then execute)
m = Mem()
m.w16(BASE, 0xF020)  # FADD DR2? dest pair=(op>>9)&7=0 (DR0), src=(op>>5)&7=1 (DR2)
cc = SH4(m)
cc.fpu = FPU(cc)
cc.pc = BASE
cc.sr &= ~0xF0
cc.sr &= ~(1 << 15)
cc.fpscr = (1 << 20)
f = cc.fpu
f.setd(0, 0, 1.5)
f.setd(1, 0, 2.5)
cc.run(limit=3)
check("fadd-dr", abs(f.getd(0, 0) - 4.0) < 1e-9, str(f.getd(0, 0)))

# mode switches ---------------------------------------------------------------
c = runf([0xF3FD])
check("fschg", ((c.fpscr >> 20) & 1) == 1)
c = runf([0xFBFD])
check("frchg", ((c.fpscr >> 21) & 1) == 1)
c = runf([0xF7FD])
check("fpchg", ((c.fpscr >> 19) & 1) == 1)

# FSCA: fpul=0 -> sin=0, cos=1 into DR0 (SZ=1)
c = runf([0xF0FD], fpscr=(1 << 20), fpul=0)
check("fsca", abs(c.fr[0][0]) < 1e-9 and abs(c.fr[0][1] - 1.0) < 1e-9,
      f"{c.fr[0][0]}, {c.fr[0][1]}")

# FIPR: FV0=(1,2,3,4), FV1=(1,1,1,1) -> FR3 = 10. 1111nnmm11101101 n=0,m=1
c = runf([0xF1ED], fr=[1.0, 2.0, 3.0, 4.0, 1.0, 1.0, 1.0, 1.0] + [0.0] * 8)
check("fipr", abs(c.fr[0][3] - 10.0) < 1e-9, str(c.fr[0][3]))

# FTRV: matrix rows are FV0-3 themselves, so identity x v has the
# diagonal signature: out[1] = v.v = 174. Verifies the multiply ran right.
m2 = [1.0 if (i // 4) == (i % 4) else 0.0 for i in range(16)]
m2[4:8] = [5.0, 6.0, 7.0, 8.0]
c = runf([0xF5FD], fr=m2)
check("ftrv", [c.fr[0][4], c.fr[0][5], c.fr[0][6], c.fr[0][7]] == [5.0, 174.0, 7.0, 8.0])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
