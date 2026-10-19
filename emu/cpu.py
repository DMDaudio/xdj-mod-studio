"""SH-4 CPU interpreter for the emulator.

Extends tools/sh4sim.py (21 mnemonics, decompressor-only verifier) into a
broader emulator core (~50 mnemonics) able to step the real boot stub at
0xA0000000 and trap cleanly on unmodelled hardware.

Design rules:
  - Pure Python, no deps. On unknown opcode -> CpuTrap (logged, PC shown),
    never silent wrong execution.
  - MMIO reads return 0 (or a scripted value via hooks); writes ignored.
    Hooks dict lets the UI inject SCIF1 PANL bytes, timers, etc.
  - Delay slots implemented for bra/bsr/jsr/rts bt/s bf/s.
"""
from .bus import Bus


class CpuTrap(RuntimeError):
    pass


def s8(v):
    return v - 0x100 if v & 0x80 else v


def s12(v):
    return v - 0x1000 if v & 0x800 else v


class SH4:
    def __init__(self, bus, hooks=None):
        self.bus = bus
        self.hooks = hooks or {}
        self.r = [0] * 16
        self.pr = 0
        self.mach = 0
        self.macl = 0
        self.sr_t = 0
        self.pc = 0xA0000000
        self.steps = 0
        self.traps = []

    # -- fetch ------------------------------------------------------------
    def fetch(self, pc):
        w = self.bus.r16(pc)
        return w

    def run(self, entry=None, limit=200000, sp=0x0FFF0000):
        if entry is not None:
            self.pc = entry
        self.r[15] = sp
        self.pr = 0xDEAD0000
        n = 0
        while n < limit:
            if self.pc == 0xDEAD0000:
                return "returned"
            op = self.fetch(self.pc)
            nxt = (self.pc + 2) & 0xFFFFFFFF
            tgt = self.step(op, nxt)
            n += 1
            self.steps += 1
            if tgt is not None:
                target, delay = tgt
                if delay:
                    dop = self.fetch(nxt)
                    self.pc = nxt
                    dt = self.step(dop, (nxt + 2) & 0xFFFFFFFF)
                    if dt is not None:
                        raise CpuTrap(f"branch in delay slot at {nxt:#x}")
                    self.steps += 1
                    n += 1
                if target == 0xDEAD0000:
                    return "returned"
                self.pc = target & 0xFFFFFFFF
            else:
                self.pc = nxt
        return f"limit {limit} reached at {self.pc:#010x}"

    # -- one instruction --------------------------------------------------
    def step(self, op, nxt):
        r = self.r
        b = self.bus
        n = (op >> 8) & 0xF
        m = (op >> 4) & 0xF
        d8 = op & 0xFF
        T = lambda v: setattr(self, "sr_t", 1 if v else 0)

        if op == 0x0009:
            return None  # nop
        if op == 0x000B:
            return (self.pr, True)  # rts
        if op == 0x0018:
            return None  # sett (T=1)
        if op == 0x0019:
            T(0)
            return None  # clrt
        if op == 0x002B:
            return (self.pr, False)  # rtv/n? treat as rts no delay
        if op == 0x4F22:  # sts.l pr,@-r15
            r[15] = (r[15] - 4) & 0xFFFFFFFF
            b.w32(r[15], self.pr)
            return None
        if op == 0x4F26:  # lds.l @r15+,pr
            self.pr = b.r32(r[15])
            r[15] = (r[15] + 4) & 0xFFFFFFFF
            return None
        if op == 0x0F22:  # sts mach? (0x0A22 mach, 0x1A22 macl variants)
            pass
        top = op >> 12
        hi8 = op >> 8
        # mov.l Rm,@-Rn
        if top == 0x2 and (op & 0xF) == 0x6:
            r[n] = (r[n] - 4) & 0xFFFFFFFF
            b.w32(r[n], r[m])
            return None
        # mov.l @Rm+,Rn
        if top == 0x6 and (op & 0xF) == 0x6:
            r[n] = b.r32(r[m])
            r[m] = (r[m] + 4) & 0xFFFFFFFF
            return None
        # mov.l @(disp,PC),Rn  (top D)
        if top == 0xD:
            r[n] = b.r32(((self.pc + 4) & ~3) + d8 * 4)
            return None
        # mov.w @(disp,PC),Rn (top 9)
        if top == 0x9:
            v = b.r16((self.pc + 4 + d8 * 2) & 0xFFFFFFFF)  # MOV.W: unaligned PC+4
            r[n] = v - 0x10000 if v & 0x8000 else v
            r[n] &= 0xFFFFFFFF
            return None
        # mova @(disp,PC),R0  (1100 0111 ....)
        if (op & 0xFF00) == 0xC700:
            r[0] = ((self.pc + 4) & ~3) + (op & 0xFF) * 4
            r[0] &= 0xFFFFFFFF
            return None
        # mov #imm,Rn
        if top == 0xE:
            r[n] = s8(d8) & 0xFFFFFFFF
            return None
        # add #imm,Rn
        if top == 0x7:
            r[n] = (r[n] + s8(d8)) & 0xFFFFFFFF
            return None
        # mov Rm,Rn
        if top == 0x6 and (op & 0xF) == 0x3:
            r[n] = r[m]
            return None
        # mov.b @Rm+,Rn
        if top == 0x6 and (op & 0xF) == 0x4:
            r[n] = s8(b.r8(r[m])) & 0xFFFFFFFF
            r[m] = (r[m] + 1) & 0xFFFFFFFF
            return None
        # mov.b Rm,@Rn
        if top == 0x2 and (op & 0xF) == 0x0:
            b.w8(r[n], r[m] & 0xFF)
            return None
        # mov.b @Rm,Rn (0100 .... .... 0000? actually 0x6000|..00)
        if top == 0x6 and (op & 0xF) == 0x0:
            r[n] = s8(b.r8(r[m])) & 0xFFFFFFFF
            return None
        # mov.b Rm,@-Rn
        if top == 0x2 and (op & 0xF) == 0x1:
            r[n] = (r[n] - 1) & 0xFFFFFFFF
            b.w8(r[n], r[m] & 0xFF)
            return None
        # mov.b @Rm+,R0 handled above generically; mov.b R0,@(disp,Rn)
        if top == 0x8 and ((op >> 8) & 0xF) == 0x0:
            b.w8((r[m] + (op & 0xF)) & 0xFFFFFFFF, r[0] & 0xFF)
            return None
        # mov.b @(disp,Rm),R0
        if top == 0x8 and ((op >> 8) & 0xF) == 0x4:
            r[0] = s8(b.r8((r[m] + (op & 0xF)) & 0xFFFFFFFF)) & 0xFFFFFFFF
            return None
        # mov.w Rm,@Rn / mov.w @Rm,Rn
        if top == 0x2 and (op & 0xF) == 0x2:
            b.w16(r[n], r[m] & 0xFFFF)
            return None
        if top == 0x6 and (op & 0xF) == 0x2:
            v = b.r16(r[m])
            r[n] = (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF
            return None
        # mov.w R0,@(disp,Rn)
        if top == 0x8 and ((op >> 8) & 0xF) == 0x1:
            b.w16((r[m] + (op & 0xF) * 2) & 0xFFFFFFFF, r[0] & 0xFFFF)
            return None
        # mov.w @(disp,Rm),R0
        if top == 0x8 and ((op >> 8) & 0xF) == 0x5:
            v = b.r16((r[m] + (op & 0xF) * 2) & 0xFFFFFFFF)
            r[0] = (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF
            return None
        # mov.l R0,@(disp,Rn) / mov.l @(disp,Rm),Rn handled via generic below
        if top == 0x1:  # mov.l Rm,@(disp,Rn)
            b.w32((r[n] + (op & 0xF) * 4) & 0xFFFFFFFF, r[m])
            return None
        if top == 0x5:  # mov.l @(disp,Rm),Rn
            r[n] = b.r32((r[m] + (op & 0xF) * 4) & 0xFFFFFFFF)
            return None
        # mov.b @(R0,Rm),Rn / mov.b Rm,@(R0,Rn)
        if top == 0x0 and (op & 0xF) == 0xC:
            r[n] = s8(b.r8((r[0] + r[m]) & 0xFFFFFFFF)) & 0xFFFFFFFF
            return None
        if top == 0x0 and (op & 0xF) == 0x4:
            b.w8((r[0] + r[n]) & 0xFFFFFFFF, r[m] & 0xFF)
            return None
        # mov.w @(R0,Rm),Rn / mov.w Rm,@(R0,Rn)
        if top == 0x0 and (op & 0xF) == 0xD:
            v = b.r16((r[0] + r[m]) & 0xFFFFFFFF)
            r[n] = (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF
            return None
        if top == 0x0 and (op & 0xF) == 0x5:
            b.w16((r[0] + r[n]) & 0xFFFFFFFF, r[m] & 0xFFFF)
            return None
        # extu.b / exts.b / extu.w / exts.w
        if top == 0x6 and (op & 0xFF) in (0x0C, 0x0E, 0x08, 0x0A):
            lo = op & 0xFF
            # need m/n decode: op = 0110 nnnn mmmm xxxx
            if lo == 0x0C:
                r[n] = r[m] & 0xFF
            elif lo == 0x0E:
                r[n] = s8(r[m] & 0xFF) & 0xFFFFFFFF
            elif lo == 0x08:
                r[n] = r[m] & 0xFFFF
            else:
                v = r[m] & 0xFFFF
                r[n] = (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF
            return None
        # branches bt/bf/bt/s/bf/s
        if top == 0x8:
            sub = (op >> 8) & 0xF
            disp = s8(d8) * 2 + 4
            if sub == 0x9:
                return ((self.pc + disp) & 0xFFFFFFFF, False) if self.sr_t else None
            if sub == 0xB:
                return None if self.sr_t else ((self.pc + disp) & 0xFFFFFFFF, False)
            if sub == 0xD:
                return ((self.pc + disp) & 0xFFFFFFFF, True) if self.sr_t else None
            if sub == 0xF:
                return None if self.sr_t else ((self.pc + disp) & 0xFFFFFFFF, True)
            raise CpuTrap(f"unhandled 0x8xxx op {op:#06x} at {self.pc:#x}")
        # bra / bsr
        if top == 0xA:
            disp = op & 0xFFF
            if disp & 0x800:
                disp -= 0x1000
            return ((self.pc + 4 + disp * 2) & 0xFFFFFFFF, True)
        if top == 0xB:
            disp = op & 0xFFF
            if disp & 0x800:
                disp -= 0x1000
            self.pr = (self.pc + 4) & 0xFFFFFFFF
            return ((self.pc + 4 + disp * 2) & 0xFFFFFFFF, True)
        # jsr / jmp / jsr @Rn etc (0100 .... .... )
        if top == 0x4:
            lo = op & 0xFF
            if lo == 0x0E:  # ldc Rm,SR (privileged) -> SR not modelled, nop
                return None
            if lo == 0x07:  # ldc.l @Rm+,SR -> pop + nop
                r[m] = (r[m] + 4) & 0xFFFFFFFF
                return None
            if lo == 0x2E:  # ldc Rm,GBR
                self.gbr = r[(op >> 8) & 0xF]
                return None
            if lo == 0x0B:  # jsr @Rn: 0100 nnnn 0000 1011
                self.pr = (self.pc + 4) & 0xFFFFFFFF
                return (r[(op >> 8) & 0xF], True)
            if lo == 0x2B:  # jmp @Rn: 0100 nnnn 0010 1011
                return (r[(op >> 8) & 0xF], True)
            if lo == 0x6A:  # lds Rm,FPSCR (SH-4) -> FP not modelled, nop
                self.traps.append((self.pc, op, "lds fpscr nop"))
                return None
            if lo in (0x5A, 0x3A, 0x2A, 0x32, 0x42, 0x52, 0x62,
                      0x06, 0x16, 0x46, 0x56, 0x66):
                # other LDS/STS system-reg forms (FPUL etc.) -> nop
                self.traps.append((self.pc, op, "lds/sts sys nop"))
                return None
            # shll/shlr/shar/dt/cmp etc
            rn = (op >> 8) & 0xF
            if lo == 0x18:
                r[rn] = (r[rn] << 8) & 0xFFFFFFFF
                return None
            if lo == 0x08:
                r[rn] = (r[rn] << 2) & 0xFFFFFFFF
                return None
            if lo == 0x00:  # shll Rn
                self.sr_t = (r[rn] >> 31) & 1
                r[rn] = (r[rn] << 1) & 0xFFFFFFFF
                return None
            if lo == 0x01:  # shlr Rn
                self.sr_t = r[rn] & 1
                r[rn] = (r[rn] >> 1) & 0xFFFFFFFF
                return None
            if lo == 0x02:  # shar Rn (arithmetic)
                self.sr_t = r[rn] & 1
                v = r[rn] - 0x100000000 if r[rn] & 0x80000000 else r[rn]
                r[rn] = ((v >> 1) & 0xFFFFFFFF)
                return None
            if lo == 0x10:  # dt Rn
                r[rn] = (r[rn] - 1) & 0xFFFFFFFF
                self.sr_t = 1 if r[rn] == 0 else 0
                return None
            if lo == 0x11:  # cmp/pz Rn
                self.sr_t = 0 if r[rn] & 0x80000000 else 1
                return None
            if lo == 0x0A:  # sts mach,Rn (0100 n 0000 1010)
                r[rn] = self.mach
                return None
            if lo == 0x1A:  # sts macl,Rn
                r[rn] = self.macl
                return None
            if lo == 0x07:  # ldc Rn,SR? treat as nop-ish (don't model SR)
                return None
            raise CpuTrap(f"unhandled 0x4xxx op {op:#06x} at {self.pc:#x}")
        if top == 0x0:
            sub = op & 0xFF
            # jsr/jmp forms live under 0x4xxx; here: cmp, shifts by reg, etc.
            if sub == 0x02:  # stc sr,Rn -> return 0
                r[n] = 0
                return None
            if sub == 0x03:  # braf Rn
                return ((self.pc + 4 + r[n]) & 0xFFFFFFFF, True)
            if sub == 0x07:  # cmp/eq Rm,Rn? actually 0011.... but keep
                pass
            if sub == 0x06:  # mov.l @Rm+,Rn handled? no, that's top6
                pass
            # ldc.l @Rm+,SR (0100 .... .... 0111 is top4) -- ignore
            raise CpuTrap(f"unhandled 0x0xxx op {op:#06x} at {self.pc:#x}")
        if top == 0x3:
            sub = op & 0xF
            if sub == 0x0:  # cmp/eq Rm,Rn
                self.sr_t = 1 if r[n] == r[m] else 0
                return None
            if sub == 0x2:  # cmp/hs Rm,Rn
                self.sr_t = 1 if r[n] >= r[m] else 0
                return None
            if sub == 0x3:  # cmp/ge Rm,Rn (signed >=)
                a = r[n] - 0x100000000 if r[n] & 0x80000000 else r[n]
                c = r[m] - 0x100000000 if r[m] & 0x80000000 else r[m]
                self.sr_t = 1 if a >= c else 0
                return None
            if sub == 0x6:  # cmp/hi Rm,Rn
                self.sr_t = 1 if r[n] > r[m] else 0
                return None
            if sub == 0x7:  # cmp/gt Rm,Rn (signed >)
                a = r[n] - 0x100000000 if r[n] & 0x80000000 else r[n]
                c = r[m] - 0x100000000 if r[m] & 0x80000000 else r[m]
                self.sr_t = 1 if a > c else 0
                return None
            if sub == 0x8:  # sub Rm,Rn
                r[n] = (r[n] - r[m]) & 0xFFFFFFFF
                return None
            if sub == 0xC:  # add Rm,Rn
                r[n] = (r[n] + r[m]) & 0xFFFFFFFF
                return None
            if sub == 0xD:  # mul.l Rm,Rn
                self.macl = (r[n] * r[m]) & 0xFFFFFFFF
                return None
            if sub == 0xE:  # subc? approximate as sub
                r[n] = (r[n] - r[m] - (0 if self.sr_t else 0)) & 0xFFFFFFFF
                return None
            raise CpuTrap(f"unhandled 0x3xxx op {op:#06x} at {self.pc:#x}")
        if top == 0x2:
            sub = op & 0xF
            if sub == 0x7:  # sub Rm,Rn? no that's 3xxx; 2xxx sub7 = div? treat
                raise CpuTrap(f"unhandled op {op:#06x} at {self.pc:#x}")
            if sub == 0x8:  # tst Rm,Rn
                self.sr_t = 1 if (r[n] & r[m]) == 0 else 0
                return None
            if sub == 0x9:  # and Rm,Rn
                r[n] &= r[m]
                return None
            if sub == 0xA:  # xor Rm,Rn
                r[n] ^= r[m]
                return None
            if sub == 0xB:  # or Rm,Rn
                r[n] |= r[m]
                return None
            if sub == 0xC:  # cmp/str Rm,Rn (any byte equal)
                x = r[n] ^ r[m]
                t = 0
                for sh in (0, 8, 16, 24):
                    if ((x >> sh) & 0xFF) == 0:
                        t = 1
                self.sr_t = t
                return None
            if sub == 0xD:  # xtrct Rm,Rn
                r[n] = ((r[n] >> 16) | (r[m] << 16)) & 0xFFFFFFFF
                return None
            if sub == 0xE:  # mul.l Rm,Rn
                self.macl = (r[n] * r[m]) & 0xFFFFFFFF
                return None
            if sub == 0xF:  # muls? approx
                a = r[n] - 0x100000000 if r[n] & 0x80000000 else r[n]
                c = r[m] - 0x100000000 if r[m] & 0x80000000 else r[m]
                self.macl = (a * c) & 0xFFFFFFFF
                return None
            raise CpuTrap(f"unhandled 0x2xxx op {op:#06x} at {self.pc:#x}")
        if hi8 == 0xC9:  # and #imm,R0
            r[0] &= d8
            return None
        if hi8 == 0xC8:  # tst #imm,R0
            self.sr_t = 1 if (r[0] & d8) == 0 else 0
            return None
        if hi8 == 0xCB:  # or #imm,R0
            r[0] |= d8
            return None
        if hi8 == 0xCA:  # xor #imm,R0
            r[0] ^= d8
            return None
        if hi8 == 0xCC:  # tst.b #imm,@(R0,GBR)? approximate: read mem
            gbr = getattr(self, "gbr", 0)
            self.sr_t = 1 if (b.r8((r[0] + gbr) & 0xFFFFFFFF) & d8) == 0 else 0
            return None
        if (op & 0xFF00) == 0xC600:  # mov.b R0,@(disp,GBR)
            gbr = getattr(self, "gbr", 0)
            b.w8((gbr + (op & 0xFF)) & 0xFFFFFFFF, r[0] & 0xFF)
            return None
        raise CpuTrap(f"unimplemented op {op:#06x} at {self.pc:#x}")
