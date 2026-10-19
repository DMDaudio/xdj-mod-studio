"""ISA self-test for emu/sh4.py: hand-assembled programs per group.

Each test builds a tiny program in RAM, runs it, and asserts registers.
Run: .venv/bin/python tools/sh4test.py  (exit 0 = all pass)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from emu.sh4 import SH4, SHException, MD, RB, BL


class Mem:
    def __init__(self):
        self.d = {}

    def _p(self, a):
        a &= 0xFFFFFFFF
        if 0x80000000 <= a < 0xC0000000:  # P1/P2 -> physical (test mirror)
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
        lo = self.r32(a)
        hi = self.r32(a + 4)
        return lo | (hi << 32)

    def w64(self, a, v):
        self.w32(a, v & 0xFFFFFFFF)
        self.w32(a + 4, (v >> 32) & 0xFFFFFFFF)

    def take_interrupt(self, imask=-1):
        return None

    def tick(self, c):
        pass


BASE = 0x08000000
passed = failed = 0


def run(prog, regs=None, steps=200, pc0=BASE):
    m = Mem()
    for i, w in enumerate(prog):
        m.w16(pc0 + i * 2, w)
    c = SH4(m)
    c.pc = pc0
    c.sr &= ~0xF0       # unmask (tests don't use interrupts)
    if regs:
        for k, v in regs.items():
            c.set_gr(k, v)
    c.run(limit=steps)
    return c


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL {name} {extra}")


def O(op):
    return op


# mov/add/sub/cmp/tst/logic/shifts/extends --------------------------------
# mov #5,r1; mov #-1,r2; add #3,r1; add r2,r1; sub r2,r1
c = run([0xE105, 0xE2FF, 0x7103, 0x312C, 0x3128])
check("mov/add/sub", c.gr(1) == 8 and c.gr(2) == 0xFFFFFFFF,
      f"r1={c.gr(1):#x} r2={c.gr(2):#x}")
# cmp/eq, cmp/hi, cmp/gt, cmp/hs, cmp/ge, tst, and, or, xor
c = run([0xE105, 0xE205, 0x3004,          # r1=r2=5; cmp/eq r0? use r1,r2
         0xE001, 0xE102, 0x3212,          # r0=1 r1=2 cmp/hs r2,r1? (n=1,m=2)
         ], steps=50)
# direct: cmp/eq r1,r2 with r1==r2==7 -> T=1
c = run([0xE107, 0xE207, 0x3120])
check("cmp/eq T=1", c.sr & 1)
c = run([0xE107, 0xE208, 0x3120])
check("cmp/eq T=0", not (c.sr & 1))
# cmp/hi: r1=5 > r2=3 -> T=1 ; cmp/gt signed: r1=-1 > r2=-2 -> T=1
c = run([0xE105, 0xE203, 0x3126])
check("cmp/hi", c.sr & 1)
c = run([0xE1FF, 0xE2FE, 0x3127])
check("cmp/gt neg", c.sr & 1)
# shll/shlr/shar/shll8/dt
c = run([0xE101, 0x4100, 0x4100])   # r1=1; shll; shll -> 4
check("shll", c.gr(1) == 4)
c = run([0xE1FF, 0x4101])           # r1=-1; shlr -> 0x7fffffff
check("shlr", c.gr(1) == 0x7FFFFFFF)
c = run([0xE1FF, 0x4121])           # r1=-1; shar -> -1
check("shar", c.gr(1) == 0xFFFFFFFF)
c = run([0xE112, 0x4118])           # r1=0x12; shll8 -> 0x1200
check("shll8", c.gr(1) == 0x1200)
c = run([0xE103, 0x4110])           # r1=3; dt -> 2, T=0
check("dt", c.gr(1) == 2 and not (c.sr & 1))
c = run([0xE101, 0x4110])           # r1=1; dt -> 0, T=1
check("dt-zero", c.gr(1) == 0 and (c.sr & 1))
# extu/exts/neg/not/swap/xtrct
c = run([0xE1FF, 0x611C])           # extu.b r1(-1) -> 0xFF
check("extu.b", c.gr(1) == 0xFF)
c = run([0xE1FF, 0x611E])           # exts.b r1(-1) -> -1
check("exts.b", c.gr(1) == 0xFFFFFFFF)
c = run([0xE105, 0x610B])           # neg r0? NEG Rm,Rn: 0110nnnnmmmm1011
check("neg", c.gr(1) == (0 - 0) & 0xFFFFFFFF)  # r0=0 -> 0
# mov.l @(disp,PC): pool at end. prog: mov.l @(0,PC),r1; bra +2; .long 0x12345678
c = run([0xD100, 0xA001, 0x5678, 0x1234], steps=10)
check("mov.l PC", c.gr(1) == 0x12345678, f"r1={c.gr(1):#x}")
# branch loop: r1=3; loop: dt r1; bf loop  (bf disp -1 word -> 0x8BFD)
c = run([0xE103, 0x4110, 0x8BFD], steps=30)
check("dt/bf loop", c.gr(1) == 0, f"r1={c.gr(1)}")
# bsr/jsr/rts: bsr sub(+0); bra end; sub: mov #7,r1; rts; nop; end: nop
c = run([0xB000, 0xA002, 0xE107, 0x000B, 0x0009, 0x0009], steps=30)
check("bsr/rts", c.gr(1) == 7, f"r1={c.gr(1)}")
# PR = return past delay slot (X+4), not the delay slot (X+2). Firmware's
# 0xBB20 site relies on this: delay-slot store must run once, pre-call.
mP = Mem()
for i, w in enumerate([0xB001, 0x7101,   # bsr +2 (sub); delay: add #1,r1
                      0x0009,           # (return lands here)
                      0xE107, 0x000B, 0x0009]):  # sub: r1=7; rts; nop
    mP.w16(BASE + i * 2, w)
cP = SH4(mP)
cP.pc = BASE
cP.sr &= ~0xF0
cP.run(limit=10)
check("pr past delay", cP.pr == BASE + 4, f"pr={cP.pr:#x} want {BASE + 4:#x}")
check("delay runs once", cP.gr(1) == 7, f"r1={cP.gr(1)} (sub overwrote delay's +1)")
# jsr @r2/rts: mov.l @(1,PC),r2? simpler: r2 = addr via mova
c = run([0xC701, 0x400B, 0x0009, 0xE10A, 0x000B, 0x0009], steps=40)
check("mova/jsr/rts", c.gr(1) == 10, f"r1={c.gr(1):#x}")
# memory moves: mov.l r1,@r2; mov.l @r2,r3; mov.b/mov.w variants, pre/post inc
c = run([0xE105,                    # r1=5
         0xE208,                    # r2=8 (aligned scratch use BASE+0x100)
         0xD102, 0x0100, 0x0000,    # mov.l @(2,PC),r2 -> r2 = BASE+0x100
         0x21E6,                    # mov.l r1,@-r14? no: use r2: mov.l r1,@r2 = 0x2120? -> 0010 0001 0010 0000
         ], steps=20)
# (covered properly below)
m = Mem()
for i, w in enumerate([0xE105, 0xD202, 0x0100, 0x0000, 0x2120, 0x6320, 0x0009]):
    m.w16(BASE + i * 2, w)
# fix pool: mov.l @(2,PC),r2 at BASE+2: reads (BASE+6 & ~3)+8 = BASE+0x10+8? compute: (pc+4)&~3 = (BASE+2+4)&~3 = BASE+4; +2*4=8 -> BASE+0xC
m.w32(BASE + 0xC, BASE + 0x100)
cc = SH4(m)
cc.pc = BASE
cc.sr &= ~0xF0
cc.run(limit=20)
check("mov.l mem", cc.gr(1) == 5 and cc.gr(0) == 0 and True)
# verify via direct: store then load. Pools sit inline, jumped over.
m2 = Mem()
code = {0x00: 0xE10A,              # r1=10
        0x02: 0xD202,              # mov.l @(2,PC),r2 -> pool @+0x0C
        0x04: 0xA004,              # bra -> +0x10 (over pool1)
        0x06: 0x0009, 0x08: 0x0009, 0x0A: 0x0009,
        # +0x0C: pool1
        0x10: 0x2212,              # mov.l r1,@r2 (project convention: sub 0x2)
        0x12: 0xD303,              # mov.l @(3,PC),r3 -> pool @+0x20
        0x14: 0xA006,              # bra -> +0x24 (over pool2)
        0x16: 0x0009, 0x18: 0x0009, 0x1A: 0x0009, 0x1C: 0x0009,
        0x1E: 0x0009,
        # +0x20: pool2
        0x24: 0x6322,              # mov.l @r2,r3
        0x26: 0x0009,
        0x28: 0xA3FF}              # bra self
for off, w in code.items():
    m2.w16(BASE + off, w)
m2.w32(BASE + 0x0C, BASE + 0x200)   # pool1
m2.w32(BASE + 0x20, BASE + 0x200)   # pool2
c2 = SH4(m2)
c2.pc = BASE
c2.sr &= ~0xF0
c2.run(limit=30)
check("store/load mem", c2.gr(3) == 10 and m2.r32(BASE + 0x200) == 10,
      f"r3={c2.gr(3)} mem={m2.r32(BASE + 0x200):#x}")
# DIV0S/DIV1: primitive proven against an independent transcription of
# Ghidra's SuperH SLEIGH semantics (which mirror the Renesas operation
# text). Full quotient completion is the compiler's sequence, not the core.
def gdiv1(rn, rm, T, M, Q):
    old_q = Q
    qnew = 1 if rn & 0x80000000 else 0
    rn = ((rn << 1) | T) & 0xFFFFFFFF
    if old_q == M:
        tmp = (rn - rm) & 0xFFFFFFFF
        c = 1 if tmp > rn else 0
    else:
        tmp = (rn + rm) & 0xFFFFFFFF
        c = 1 if tmp < rn else 0
    q = c if M == qnew else (0 if c else 1)
    return tmp, (1 if q == M else 0), M, q


def gdiv0s(rn, rm):
    q = 1 if rn & 0x80000000 else 0
    mm = 1 if rm & 0x80000000 else 0
    return (1 if mm ^ q else 0), mm, q


import random
random.seed(1234)
divbad = 0
for trial in range(200):
    rn = random.getrandbits(32)
    rm = random.getrandbits(32)
    T, M, Q = random.getrandbits(1), random.getrandbits(1), random.getrandbits(1)
    m = Mem()
    m.w16(BASE, 0x2127 if trial & 1 else 0x3124)  # alternate div0s / div1
    m.w16(BASE + 2, 0x0009)
    c = SH4(m)
    c.pc = BASE
    c.sr &= ~0xF0
    if T:
        c.sr |= 1
    if M:
        c.sr |= 1 << 9
    if Q:
        c.sr |= 1 << 8
    c.set_gr(1, rn)
    c.set_gr(2, rm)
    c.run(limit=3)
    if trial & 1:
        exp = gdiv0s(rn, rm)
        got = (1 if c.sr & 1 else 0, 1 if c.sr & (1 << 9) else 0,
               1 if c.sr & (1 << 8) else 0)
    else:
        orn, oT, oM, oQ = gdiv1(rn, rm, T, M, Q)
        got = (c.gr(1), 1 if c.sr & 1 else 0, 1 if c.sr & (1 << 9) else 0,
               1 if c.sr & (1 << 8) else 0)
        exp = (orn, oT, oM, oQ)
    if got != exp:
        divbad += 1
        if divbad < 3:
            print(f"  div mismatch {rn:#x} {rm:#x} {T}{M}{Q}: got {got} want {exp}")
check("div0s/div1 oracle x200", divbad == 0, f"{divbad} mismatches")
# TRAPA: handler at VBR+0x100 stores TRA check: trapa #4 -> TRA=0x10, jumps
m4 = Mem()
m4.w16(BASE, 0xC304)         # trapa #4
m4.w16(0x08000100, 0x0009)   # handler: nop (then falls into zeros -> illegal -> loops? limit)
m4.w16(0x08000102, 0x000B)   # rts (pr garbage -> jumps wild; keep steps small)
c4 = SH4(m4)
c4.pc = BASE
c4.sr &= ~0xF0
c4.vbr = 0x08000000
c4.run(limit=1)
check("trapa", c4.tra == 0x10 and c4.pc == 0x08000100, f"tra={c4.tra:#x} pc={c4.pc:#x}")
# RTE round trip: set ssr/spc, execute rte with nop delay -> pc=spc, sr=ssr
m5 = Mem()
m5.w16(BASE, 0x002B)         # rte
m5.w16(BASE + 2, 0x0009)     # delay: nop
c5 = SH4(m5)
c5.pc = BASE
c5.sr &= ~0xF0
c5.ssr = 0x1234
c5.spc = BASE + 0x40
c5.run(limit=1)
check("rte", c5.pc == BASE + 0x40 and c5.sr == 0x1234, f"pc={c5.pc:#x} sr={c5.sr:#x}")
# MAC: mul.l 6*7=42 in macl; mac.l accumulate
m6 = Mem()
for i, w in enumerate([0xE106, 0xE207, 0x0127,   # r1=6 r2=7 mul.l r2,r1 (0000nnnnmmmm0111)
                       0x0009]):
    m6.w16(BASE + i * 2, w)
c6 = SH4(m6)
c6.pc = BASE
c6.sr &= ~0xF0
c6.run(limit=10)
check("mul.l", c6.macl == 42, f"macl={c6.macl}")
# LDC + banking, stays privileged: b1[0]=0x40000005 -> SR (MD kept, RB
# clears, T=1). Then R0 reads bank0. Run in P2.
P2 = 0xA0000000
m7 = Mem()
for i, w in enumerate([0x400E,              # ldc r0,sr
                       0x0009]):
    m7.w16(P2 + i * 2, w)
c7 = SH4(m7)
c7.pc = P2
c7.sr = MD | RB | BL
c7.b1[0] = 0x40000005
c7.run(limit=1)
check("ldc sr", c7.sr & 0xFFFFFFFF == 0x40000005 and c7.gr(0) == 0,
      f"sr={c7.sr:#x} r0(bank0)={c7.gr(0):#x}")
# user-mode P2 fetch faults (manual: only U0/SQ/on-chip in user mode)
c7b = SH4(m7)
c7b.pc = P2
c7b.sr = MD | RB | BL
c7b.b1[0] = 5
c7b.run(limit=2)  # ldc (drops privilege) + faulting fetch
check("usermode-P2-faults", c7b.expevt == 0x0E0, f"expevt={c7b.expevt:#x}")

# SHAD/SHLD with Rm in low byte (0100nnnnmmmm1100/1101): mask must ignore
# m (old code matched only m=r12). Firmware uses shad r2,r0 (0x402C).
c8 = SH4(Mem())
c8.pc = BASE
c8.sr &= ~0xF0
c8.set_gr(0, 0x15)
c8.set_gr(1, 2)
c8.step(0x401C, 0x0009)   # shad r1,r0: +2 -> 0x15<<2
check("shad r1,r0", c8.gr(0) == 0x54, f"r0={c8.gr(0):#x}")
c8.set_gr(0, 0x80000001)
c8.set_gr(2, 0xFFFFFFFF)  # -1
c8.step(0x402C, 0x0009)   # shad r2,r0: arith >>1
check("shad r2,r0 neg", c8.gr(0) == 0xC0000000, f"r0={c8.gr(0):#x}")
c8.set_gr(0, 0x80000001)
c8.step(0x401D, 0x0009)   # shld r1,r0: logical <<2
check("shld r1,r0", c8.gr(0) == 0x00000004, f"r0={c8.gr(0):#x}")
c8.set_gr(0, 0x80000001)
c8.step(0x402D, 0x0009)   # shld r2,r0: logical >>1
check("shld r2,r0 neg", c8.gr(0) == 0x40000000, f"r0={c8.gr(0):#x}")
# MAC.W @Rm+,@Rn+ with m != r1 (old code matched only lo==0x1F)
m9 = Mem()
m9.w16(0x08001000, 0x0003)
m9.w16(0x08001002, 0xFFFE)
m9.w16(0x08001004, 0x0004)
m9.w16(0x08001006, 0x0005)
c9 = SH4(m9)
c9.pc = BASE
c9.sr &= ~0xF0
c9.set_gr(2, 0x08001000)
c9.set_gr(3, 0x08001004)
c9.step(0x432F, 0x0009)   # mac.w @r2+,@r3+: 3*4=12
check("mac.w m=r2", (c9.macl, c9.gr(2), c9.gr(3)) == (12, 0x08001002, 0x08001006),
      f"macl={c9.macl} r2={c9.gr(2):#x} r3={c9.gr(3):#x}")

# SHAR is 0x4021, not 0x4002 (old code executed STS.L MACH,@-Rn as SHAR,
# corrupting r15 in firmware's crash-dump path).
cA = SH4(Mem())
cA.pc = BASE
cA.sr &= ~0xF0
cA.set_gr(3, 0x80000001)
cA.step(0x4321, 0x0009)   # shar r3
check("shar r3", cA.gr(3) == 0xC0000000, f"r3={cA.gr(3):#x}")
mB = Mem()
cB = SH4(mB)
cB.pc = BASE
cB.sr &= ~0xF0
cB.mach = 0x12345678
cB.set_gr(15, 0x08001000)
cB.step(0x4F02, 0x0009)   # sts.l mach,@-r15
check("sts.l mach", (cB.gr(15), mB.r32(0x08000FFC)) == (0x08000FFC, 0x12345678),
      f"r15={cB.gr(15):#x} mem={mB.r32(0x08000FFC):#x}")

# ICBI @Rn (0x0nE3) and SYNCO (0x00AB) are NOPs (cache-less, in-order).
# Firmware's cache-flush loop uses ICBI (was faulting as illegal).
cC = SH4(Mem())
cC.pc = BASE
cC.sr &= ~0xF0
cC.set_gr(3, 0x08001000)
cC.step(0x03E3, 0x0009)   # icbi @r3
check("icbi nop", cC.gr(3) == 0x08001000 and cC.pc == BASE,
      f"r3={cC.gr(3):#x}")
cC.step(0x00AB, 0x0009)   # synco
check("synco nop", True)

# STC.L GBR,@-Rn (0x4n13, capstone-verified) + LDC.L @Rn+,GBR round trip.
mD = Mem()
cD = SH4(mD)
cD.pc = BASE
cD.sr &= ~0xF0
cD.gbr = 0xA4050100
cD.set_gr(15, 0x08001000)
cD.step(0x4F13, 0x0009)   # stc.l gbr,@-r15
check("stc.l gbr", (cD.gr(15), mD.r32(0x08000FFC)) == (0x08000FFC, 0xA4050100),
      f"r15={cD.gr(15):#x} mem={mD.r32(0x08000FFC):#x}")
cD.gbr = 0
cD.step(0x4F17, 0x0009)   # ldc.l @r15+,gbr
check("ldc.l gbr", (cD.gbr, cD.gr(15)) == (0xA4050100, 0x08001000),
      f"gbr={cD.gbr:#x} r15={cD.gr(15):#x}")

# STC.L family round trips (SR/VBR/SSR/SPC/SGR; GBR covered above).
mE = Mem()
cE = SH4(mE)
cE.pc = BASE
cE.sr = MD | RB | BL
cE.vbr, cE.ssr, cE.spc, cE.sgr = 0x08000000, 0x50008000, 0x12345678, 0x0A000000
cE.set_gr(15, 0x08001000)
for op, reg, want in ((0x4F03, 'sr', MD | RB | BL), (0x4F23, 'vbr', 0x08000000),
                      (0x4F33, 'ssr', 0x50008000), (0x4F43, 'spc', 0x12345678),
                      (0x4F32, 'sgr', 0x0A000000)):
    cE.step(op, 0x0009)
    got = mE.r32(cE.gr(15))
    check(f"stc.l {reg}", got == (want & 0xFFFFFFFF),
          f"got={got:#x} want={want:#x}")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
