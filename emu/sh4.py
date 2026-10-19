"""Full SH-4 core: complete integer ISA, MMU, exceptions, interrupts.

Reference: R01UH0174EJ (SH7724 hardware manual) Sections 2-5 + Tables 3.x.
Two corrections to the earlier emu/cpu.py this supersedes (both proven from
the manual's bit tables, not memory):
  - STS MACH/MACL/PR,Rn is 0000nnnn........ (top nibble 0), NOT 0100. The
    0100 forms are LDS. The old core silently dropped every MACH/MACL write.
  - RTE (0x002B) HAS a delay slot ("branch instructions and RTE ... are
    delayed branches", Sec 3.1). TRAPA has none.

Memory model: self.mem must provide r8/r16/r32/r64/w8/w16/w32/w64 on
PHYSICAL addresses, plus take_interrupt() -> (level, code) | None and
tick(cycles). Translation (P1/P2/P3/P4 + UTLB) lives here.
"""
MD = 1 << 30
RB = 1 << 29
BL = 1 << 28
FD = 1 << 15
M = 1 << 9
Q = 1 << 8
IMASK = 0xF0
SBIT = 1 << 1
TBIT = 1


class SHException(Exception):
    """Raised internally on precise faults; always converted to a vector."""
    def __init__(self, offset, code, tea=None, use_expevt=True):
        super().__init__(f"exc vec+{offset:#x} code={code:#x}")
        self.offset = offset
        self.code = code
        self.tea = tea
        self.use_expevt = use_expevt


class PcHalt(Exception):
    """Raised by a trace hook to stop the run immediately, state intact."""
    def __init__(self, pc):
        super().__init__(f"pc halt at {pc:#x}")
        self.pc = pc


def _s8(v):
    return v - 0x100 if v & 0x80 else v


def _s12(v):
    return v - 0x1000 if v & 0x800 else v


class SH4:
    def __init__(self, mem):
        self.mem = mem
        self.b0 = [0] * 8          # R0-R7 bank 0
        self.b1 = [0] * 8          # R0-R7 bank 1
        self.rhi = [0] * 8         # R8-R15 (R15 = stack)
        self.pc = 0xA0000000
        self.pr = 0
        self.gbr = 0
        self.vbr = 0
        self.mach = 0
        self.macl = 0
        self.sr = MD | RB | BL | 0xF0 | FD   # reset state: MD/RB/BL=1 IMASK=F FD=1
        self.ssr = 0
        self.spc = 0
        self.sgr = 0
        self.dbr = 0
        self.tra = 0
        self.expevt = 0
        self.intevt = 0
        self.tea = 0
        self.pteh = 0
        self.ptel = 0
        self.ptea = 0
        self.ttb = 0
        self.mmucr = 0
        self.ccr = 0
        self.fpscr = 0x00040000 if False else 0  # DN bit? keep 0
        self.fpul = 0
        self.fr = [[0.0] * 16, [0.0] * 16]       # float banks
        self.utlb = []             # list of dicts {vpn,ppn,sz,asid,sh,pr,d,v}
        self.sleeping = False
        self.steps = 0
        self.cycles = 0
        # FPU mixin hook (emu/sh4fpu.py); None -> FPU ops raise FPU exception
        self.fpu = None
        self.trace = None          # optional callable(pc, op)
        self.native_hook = None    # optional fn(cpu, pc) -> True if emulated

    # -- registers ------------------------------------------------------
    def gr(self, i):
        if i < 8:
            return self.b1[i] if self.sr & RB else self.b0[i]
        return self.rhi[i - 8]

    def set_gr(self, i, v):
        v &= 0xFFFFFFFF
        if i < 8:
            if self.sr & RB:
                self.b1[i] = v
            else:
                self.b0[i] = v
        else:
            self.rhi[i - 8] = v

    def t(self):
        return self.sr & TBIT

    def set_t(self, v):
        self.sr = (self.sr | TBIT) if v else (self.sr & ~TBIT)

    def imask(self):
        return (self.sr >> 4) & 0xF

    # -- MMU ------------------------------------------------------------
    def mmu_on(self):
        return bool(self.mmucr & 1)  # MMUCR.AT

    def translate(self, addr, write=False, fetch=False):
        addr &= 0xFFFFFFFF
        a = addr
        if 0x80000000 <= a < 0xA0000000:      # P1 cached
            if not (self.sr & MD):
                raise SHException(0x100, 0x0E0)
            return a & 0x1FFFFFFF
        if 0xA0000000 <= a < 0xC0000000:      # P2 uncached
            if not (self.sr & MD):
                raise SHException(0x100, 0x0E0)
            return a & 0x1FFFFFFF
        if 0xE0000000 <= a <= 0xFFFFFFFF:     # P4 direct (control + SQ)
            if not (self.sr & MD):
                raise SHException(0x100, 0x0E0)  # address error in user mode
            return a
        if not self.mmu_on():
            # Manual Sec 7: with AT=0 there are no MMU exceptions; P0/U0 low
            # 512 MB are direct-mapped, P3 clears to physical. The 0x20000000-
            # 0x7FFFFFFF window is reserved (address error), P1/P2 need MD.
            if self.sr & MD:
                if a < 0x20000000:
                    return a                       # P0/U0 direct
                if 0xC0000000 <= a < 0xE0000000:    # P3 -> physical
                    return a & 0x1FFFFFFF
                raise SHException(0x100, 0x0E0, tea=a)  # reserved window
            else:
                if a < 0x20000000:
                    return a                       # U0 direct in user mode
                if 0xE0000000 <= a < 0xE0000040:
                    return a                       # store queues
                raise SHException(0x100, 0x0E0)  # address error: priv area
        # MMU on: UTLB lookup for U0/P3 (P4 handled above)
        asid = self.pteh & 0xFF
        for e in self.utlb:
            size = {0: 1024, 1: 4096, 2: 65536, 3: 1048576}[e["sz"]]
            mask = ~(size - 1) & 0xFFFFFFFF
            if (a & mask) == (e["vpn"] & mask) and (e["sh"] or e["asid"] == asid):
                if write and not e["d"]:
                    raise SHException(0x400, 0x080, tea=a)  # initial page write
                if not e["pr"]:
                    raise SHException(0x400, 0x080, tea=a)  # protection violation
                off = a & (size - 1)
                return (e["ppn"] & mask) | off
        raise SHException(0x400, 0x060 if write else 0x040, tea=a)

    # -- memory (translated, alignment-checked) -------------------------
    def _rd(self, a, n, write=False, fetch=False):
        try:
            p = self.translate(a, write=write, fetch=fetch)
        except SHException as e:
            e.tea = a & 0xFFFFFFFF
            raise
        if n == 1:
            return self.mem.r8(p)
        if n == 2:
            if a & 1:
                raise SHException(0x100, 0x0E0, tea=a & 0xFFFFFFFF)
            return self.mem.r16(p)
        if n == 4:
            if a & 3:
                raise SHException(0x100, 0x0E0, tea=a & 0xFFFFFFFF)
            return self.mem.r32(p)
        if a & 7:
            raise SHException(0x100, 0x0E0, tea=a & 0xFFFFFFFF)
        return self.mem.r64(p)

    def _wr(self, a, n, v):
        try:
            p = self.translate(a, write=True)
        except SHException as e:
            e.tea = a & 0xFFFFFFFF
            raise
        if n == 1:
            self.mem.w8(p, v & 0xFF)
        elif n == 2:
            if a & 1:
                raise SHException(0x100, 0x0E0, tea=a & 0xFFFFFFFF)
            self.mem.w16(p, v & 0xFFFF)
        elif n == 4:
            if a & 3:
                raise SHException(0x100, 0x0E0, tea=a & 0xFFFFFFFF)
            self.mem.w32(p, v & 0xFFFFFFFF)
        else:
            if a & 7:
                raise SHException(0x100, 0x0E0, tea=a & 0xFFFFFFFF)
            self.mem.w64(p, v & 0xFFFFFFFFFFFFFFFF)

    def r8(self, a):
        return self._rd(a, 1)

    def r16(self, a):
        v = self._rd(a, 2)
        return v - 0x10000 if v & 0x8000 else v

    def r16u(self, a):
        return self._rd(a, 2)

    def r32(self, a):
        return self._rd(a, 4)

    def w8(self, a, v):
        self._wr(a, 1, v)

    def w16(self, a, v):
        self._wr(a, 2, v)

    def w32(self, a, v):
        self._wr(a, 4, v)

    def ldtlb(self):
        e = {"vpn": self.pteh & 0xFFFFFC00,
             "asid": self.pteh & 0xFF,
             "ppn": self.ptel & 0x1FFFFFFC,
             "sz": (self.ptel >> 4) & 3,
             "sh": bool(self.ptel & 2),
             "d": bool(self.ptel & 1),
             "pr": (self.ptel >> 6) & 3}
        urb = (self.mmucr >> 10) & 0x3F
        for i, o in enumerate(self.utlb):
            if o.get("idx") == urb:
                self.utlb[i] = dict(e, idx=urb)
                break
        else:
            if len(self.utlb) >= 64:
                self.utlb.pop(0)
            self.utlb.append(dict(e, idx=urb))

    # -- exceptions -------------------------------------------------------
    def enter_exc(self, offset, code, spc, use_expevt=True, tea=None):
        self.spc = spc & 0xFFFFFFFF
        self.ssr = self.sr
        self.sgr = self.gr(15)
        if use_expevt:
            self.expevt = code
        else:
            self.intevt = code
        if tea is not None:
            self.tea = tea & 0xFFFFFFFF
        self.sr |= MD | RB | BL
        self.pc = (self.vbr + offset) & 0xFFFFFFFF

    def enter_interrupt(self, level, code):
        self.spc = self.pc
        self.ssr = self.sr
        self.sgr = self.gr(15)
        self.intevt = code
        self.sr |= MD | RB | BL
        self.sr = (self.sr & ~IMASK) | ((level & 0xF) << 4)
        self.pc = (self.vbr + 0x600) & 0xFFFFFFFF
        self.sleeping = False       # an accepted interrupt ends SLEEP

    def check_interrupt(self):
        if self.sr & BL:
            return
        # the INTC only hands over a source whose level beats IMASK; the
        # rest stay pending (discarding them here lost 2DG/DMA completions)
        got = self.mem.take_interrupt(self.imask())
        if got is None:
            return
        level, code = got
        self.enter_interrupt(level, code)

    # -- pickling (drop trace lambda, fpu, mem, exc wrapper; restored) -----
    def __getstate__(self):
        d = dict(self.__dict__)
        d.pop("trace", None)
        d.pop("native_hook", None)  # reattached by machine (like trace)
        d.pop("fpu", None)  # rebuilt by machine (fresh FPU, stateless)
        d.pop("mem", None)  # reattached by machine
        d.pop("enter_exc", None)  # re-wrapped by machine._hook_exc
        return d

    def __setstate__(self, d):
        d = dict(d)
        raw = d.pop("fr_raw", None)     # exact FR bits from the C core
        if raw:
            import struct
            d["fr"] = [[struct.unpack("<f", struct.pack("<I", raw[b * 16 + i]))[0]
                        for i in range(16)] for b in range(2)]
        self.__dict__.update(d)
        self.trace = None
        self.native_hook = None
        self.fpu = None

    # -- reset --------------------------------------------------------------
    def reset(self):
        self.b0 = [0] * 8
        self.b1 = [0] * 8
        self.rhi = [0] * 8
        self.pc = 0xA0000000
        self.pr = 0
        self.sr = MD | RB | BL | 0xF0 | FD
        self.vbr = 0
        self.sleeping = False

    # -- run ------------------------------------------------------------------
    def run(self, limit=1000000):
        n = 0
        while n < limit:
            if self.sleeping:
                self.mem.tick(100)
                self.check_interrupt()
                if self.sleeping:
                    n += 1
                    continue
            else:
                self.check_interrupt()
            pc = self.pc
            nh = self.native_hook
            if nh is not None and nh(self, pc):
                n += 1
                if self.mem.tick is not None and (n & 0xFF) == 0:
                    self.mem.tick(256)
                continue
            try:
                op = self._rd(pc, 2, fetch=True)
            except SHException as e:
                self.enter_exc(e.offset, e.code, pc,
                               e.use_expevt, e.tea)
                n += 1
                continue
            if self.trace is not None:
                try:
                    self.trace(pc, op)
                except PcHalt as h:
                    return f"pc halt at {h.pc:#010x}"
            nxt = (pc + 2) & 0xFFFFFFFF
            try:
                res = self.step(op, nxt)
                self.steps += 1
                if res is not None:
                    tgt, delay, cyc = res
                    self.cycles += cyc
                    if delay:
                        dop = self._rd(nxt, 2, fetch=True)
                        self.pc = nxt
                        dres = self.step(dop, (nxt + 2) & 0xFFFFFFFF)
                        # (None, False, cyc) is a timed non-branch op such
                        # as MUL.L/DMULS.L/MAC -- legal in a delay slot
                        if dres is not None and not (
                                isinstance(dres, tuple) and
                                (dres[0] == "adv4" or dres[0] is None)):
                            # branch (or RTE) in delay slot
                            self.enter_exc(0x100, 0x1A0, nxt)
                        else:
                            self.steps += 1
                            if isinstance(dres, tuple) and dres[0] == "adv4":
                                self.pc = (nxt + 2) & 0xFFFFFFFF
                    if isinstance(tgt, tuple):
                        # RTE: delay slot ran; now restore SSR->SR, SPC->PC
                        _, ssr, _ = tgt
                        self.sr = ssr
                        self.pc = tgt[0]
                    elif tgt == "exc":
                        pass    # TRAPA already vectored; pc is set
                    elif tgt == "adv4":
                        self.pc = (self.pc + 4) & 0xFFFFFFFF
                    elif tgt is not None:
                        self.pc = tgt
                    else:
                        self.pc = nxt
                else:
                    self.pc = nxt
                    self.cycles += 1
            except SHException as e:
                self.enter_exc(e.offset, e.code, pc,
                               e.use_expevt, e.tea)
            n += 1
            if self.mem.tick is not None and (n & 0xFF) == 0:
                self.mem.tick(256)
        return f"limit {limit} reached at {self.pc:#010x}"

    # -- one instruction --------------------------------------------------
    # Returns None (fall through) or (target, delay_slot, cycles).
    # For RTE the "target" is a (pc, ssr, sgr) triple: the delay slot runs
    # first, then SSR->SR/SPC->PC are restored.
    def step(self, op, nxt):
        r = self.gr
        w = self.set_gr
        n = (op >> 8) & 0xF
        m = (op >> 4) & 0xF
        i8 = op & 0xFF
        T = self.set_t

        if op & 0xF000 == 0xF000:
            if self.fpu is not None:
                return self.fpu.step(op, nxt)
            if self.sr & FD:
                raise SHException(0x100, 0x120)
            raise SHException(0x100, 0x180)

        top = op >> 12
        # ---- 0x0xxx ------------------------------------------------------
        if top == 0x0:
            lo = op & 0xFF
            if (op & 0x0F) == 0x07:  # MUL.L Rm,Rn -> MACL (0000nnnnmmmm0111)
                self.macl = (r(n) * r(m)) & 0xFFFFFFFF
                return (None, False, 2)
            if (op & 0x0F) == 0x0F:  # MAC.L @Rm+,@Rn+ (0000nnnnmmmm1111)
                a = self._rd(r(m), 4)
                b = self._rd(r(n), 4)
                w(m, (r(m) + 4) & 0xFFFFFFFF)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                acc = ((self.mach << 32) | self.macl)
                acc = acc - 0x10000000000000000 if acc & 0x8000000000000000 else acc
                a = a - 0x100000000 if a & 0x80000000 else a
                b = b - 0x100000000 if b & 0x80000000 else b
                acc = (acc + a * b) & 0xFFFFFFFFFFFFFFFF
                self.mach = (acc >> 32) & 0xFFFFFFFF
                self.macl = acc & 0xFFFFFFFF
                return (None, False, 3)
            if lo == 0x02:      # STC SR,Rn
                w(n, self.sr)
                return None
            if lo == 0x03:      # BSRF Rn (PR = return past delay slot)
                self.pr = (nxt + 2) & 0xFFFFFFFF
                return ((self.pc + 4 + r(n)) & 0xFFFFFFFF, True, 2)
            if lo == 0x08:      # CLRT
                T(0)
                return None
            if lo == 0x09:      # NOP
                return None
            if lo == 0x0B:      # RTS
                return (self.pr, True, 2)
            if lo == 0x0A:      # STS MACH,Rn
                w(n, self.mach)
                return None
            if lo == 0x18:      # SETT
                T(1)
                return None
            if lo == 0x19:      # DIV0U
                self.sr &= ~(M | Q | TBIT)
                return None
            if lo == 0x1A:      # STS MACL,Rn
                w(n, self.macl)
                return None
            if lo == 0x1B:      # SLEEP
                self.sleeping = True
                return None
            if lo == 0x23:      # BRAF Rn (0000nnnn00100011)
                return ((self.pc + 4 + r(n)) & 0xFFFFFFFF, True, 2)
            if lo == 0x28:      # CLRMAC
                self.mach = self.macl = 0
                return None
            if lo == 0x29:      # MOVT Rn
                w(n, 1 if self.t() else 0)
                return None
            if lo == 0x2A:      # STS PR,Rn
                w(n, self.pr)
                return None
            if lo == 0x5A:      # STS FPUL,Rn (0000nnnn01011010)
                w(n, self.fpul)
                return None
            if lo == 0x6A:      # STS FPSCR,Rn (0000nnnn01101010)
                w(n, self.fpscr)
                return None
            if lo == 0x2B:      # RTE (delayed branch; restores after slot)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                return ((self.spc, self.ssr, self.sgr), True, 4)
            if lo == 0x38:      # LDTLB
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.ldtlb()
                return None
            if lo == 0x48:      # CLRS
                self.sr &= ~SBIT
                return None
            if lo == 0x58:      # SETS
                self.sr |= SBIT
                return None
            sub = op & 0xF
            if sub == 0xC:      # MOV.B @(R0,Rm),Rn
                w(n, _s8(self.r8((r(0) + r(m)) & 0xFFFFFFFF)) & 0xFFFFFFFF)
                return None
            if sub == 0xD:      # MOV.W @(R0,Rm),Rn
                v = self._rd((r(0) + r(m)) & 0xFFFFFFFF, 2)
                w(n, (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
                return None
            if sub == 0xE:      # MOV.L @(R0,Rm),Rn
                w(n, self._rd((r(0) + r(m)) & 0xFFFFFFFF, 4))
                return None
            if sub == 0x4:      # MOV.B Rm,@(R0,Rn)
                self.w8((r(0) + r(n)) & 0xFFFFFFFF, r(m) & 0xFF)
                return None
            if sub == 0x5:      # MOV.W Rm,@(R0,Rn)
                self._wr((r(0) + r(n)) & 0xFFFFFFFF, 2, r(m))
                return None
            if sub == 0x6:      # MOV.L Rm,@(R0,Rn)
                self._wr((r(0) + r(n)) & 0xFFFFFFFF, 4, r(m))
                return None
            if lo == 0x12:      # STC GBR,Rn
                w(n, self.gbr)
                return None
            if lo == 0x22:      # STC VBR,Rn
                w(n, self.vbr)
                return None
            if lo == 0x32:      # STC SSR,Rn
                w(n, self.ssr)
                return None
            if lo == 0x42:      # STC SPC,Rn
                w(n, self.spc)
                return None
            if lo == 0x3A:      # STC SGR,Rn
                w(n, self.sgr)
                return None
            if lo == 0xFA:      # STC DBR,Rn
                w(n, self.dbr)
                return None
            if (op & 0x008F) == 0x0082:  # STC Rm_BANK,Rn (0000nnnn1mmm0010)
                # Rm_BANK is the INACTIVE bank (RB=1 -> bank0). Mask must not
                # include the destination field (was 0x0F8F: only Rn=r0 matched).
                bn = (op >> 4) & 0x7
                w(n, (self.b0 if self.sr & RB else self.b1)[bn])
                return None
            if lo == 0xC3:      # MOVCA.L R0,@Rn
                self._wr(r(n), 4, r(0))
                return None
            if lo in (0x83, 0x93, 0xA3, 0xB3, 0xD3, 0xE3):
                return None     # PREF/PREFI/OCBI/OCBP/OCBWB/ICBI @Rn
            if lo == 0xAB:      # SYNCO (SH4A barrier; NOP in-order, no caches)
                return None
            raise SHException(0x100, 0x180)

        # ---- 0x1xxx: MOV.L Rm,@(disp,Rn) ------------------------------------
        if top == 0x1:
            self._wr((r(n) + (op & 0xF) * 4) & 0xFFFFFFFF, 4, r(m))
            return None
        # ---- 0x5xxx: MOV.L @(disp,Rm),Rn ------------------------------------
        if top == 0x5:
            w(n, self._rd((r(m) + (op & 0xF) * 4) & 0xFFFFFFFF, 4))
            return None
        # ---- 0x2xxx ----------------------------------------------------------
        if top == 0x2:
            sub = op & 0xF
            # NOTE (2026-09-28): sub 0x0/0x1/0x2 ARE the MOV.B/W/L Rm,@Rn
            # stores (matches capstone, fastcore.c, and the firmware itself,
            # e.g. 0x2742 = mov.l r4,@r7 inline in the PANL parser which runs
            # every panel cycle on hardware). An earlier "fix" to sub
            # 0xC/D/E broke the parser oracle; reverted. Do NOT touch
            # without hardware evidence.
            if sub == 0x0:      # MOV.B Rm,@Rn
                self.w8(r(n), r(m) & 0xFF)
                return None
            if sub == 0x1:      # MOV.W Rm,@Rn
                self._wr(r(n), 2, r(m))
                return None
            if sub == 0x2:      # MOV.L Rm,@Rn
                self._wr(r(n), 4, r(m))
                return None
            if sub == 0x3:      # (unassigned)
                raise SHException(0x100, 0x180)
            if sub == 0x4:      # MOV.B Rm,@-Rn
                w(n, (r(n) - 1) & 0xFFFFFFFF)
                self.w8(r(n), r(m) & 0xFF)
                return None
            if sub == 0x5:      # MOV.W Rm,@-Rn
                w(n, (r(n) - 2) & 0xFFFFFFFF)
                self._wr(r(n), 2, r(m))
                return None
            if sub == 0x6:      # MOV.L Rm,@-Rn
                w(n, (r(n) - 4) & 0xFFFFFFFF)
                self._wr(r(n), 4, r(m))
                return None
            if sub == 0x7:      # DIV0S Rm,Rn: MSB Rn->Q, MSB Rm->M, M^Q->T
                q = 1 if r(n) & 0x80000000 else 0
                mm = 1 if r(m) & 0x80000000 else 0
                if q:
                    self.sr |= Q
                else:
                    self.sr &= ~Q
                if mm:
                    self.sr |= M
                else:
                    self.sr &= ~M
                T(1 if (mm ^ q) else 0)
                return None
            if sub == 0x8:      # TST Rm,Rn
                T(1 if (r(n) & r(m)) == 0 else 0)
                return None
            if sub == 0x9:      # AND Rm,Rn
                w(n, r(n) & r(m))
                return None
            if sub == 0xA:      # XOR Rm,Rn
                w(n, r(n) ^ r(m))
                return None
            if sub == 0xB:      # OR Rm,Rn
                w(n, r(n) | r(m))
                return None
            if sub == 0xC:      # CMP/STR Rm,Rn
                x = r(n) ^ r(m)
                t = 0
                for sh in (0, 8, 16, 24):
                    if ((x >> sh) & 0xFF) == 0:
                        t = 1
                        break
                T(t)
                return None
            if sub == 0xD:      # XTRCT Rm,Rn
                w(n, ((r(n) >> 16) | (r(m) << 16)) & 0xFFFFFFFF)
                return None
            if sub == 0xE:      # MULU.W Rm,Rn -> MACL
                self.macl = ((r(n) & 0xFFFF) * (r(m) & 0xFFFF)) & 0xFFFFFFFF
                return (None, False, 3)
            if sub == 0xF:      # MULS.W Rm,Rn -> MACL
                a = r(n) & 0xFFFF
                b = r(m) & 0xFFFF
                a = a - 0x10000 if a & 0x8000 else a
                b = b - 0x10000 if b & 0x8000 else b
                self.macl = (a * b) & 0xFFFFFFFF
                return (None, False, 3)
            raise SHException(0x100, 0x180)
        # ---- 0x3xxx: ALU -------------------------------------------------------
        if top == 0x3:
            sub = op & 0xF
            if sub == 0x1:      # 32-bit FMOV prefix (SH-4A): 0011nnnnmmmm0001
                w2 = self._rd(nxt, 2)
                if self.fpu is None:
                    raise SHException(0x100, 0x180)
                return self.fpu.fmov32(op, w2)
            if sub == 0x0:      # CMP/EQ
                T(1 if r(n) == r(m) else 0)
                return None
            if sub == 0x2:      # CMP/HS (unsigned >=)
                T(1 if r(n) >= r(m) else 0)
                return None
            if sub == 0x3:      # CMP/GE (signed >=)
                a = r(n) - 0x100000000 if r(n) & 0x80000000 else r(n)
                b = r(m) - 0x100000000 if r(m) & 0x80000000 else r(m)
                T(1 if a >= b else 0)
                return None
            if sub == 0x4:      # DIV1 (1-step Rn/Rm; cross-checked
                # against Ghidra SuperH SLEIGH + manual Table 3.5)
                old_q = 1 if self.sr & Q else 0
                mm = 1 if self.sr & M else 0
                q_new = 1 if r(n) & 0x80000000 else 0
                rn = ((r(n) << 1) | (1 if self.t() else 0)) & 0xFFFFFFFF
                if old_q == mm:
                    tmp = (rn - r(m)) & 0xFFFFFFFF
                    c = 1 if tmp > rn else 0     # unsigned wrap = borrow
                else:
                    tmp = (rn + r(m)) & 0xFFFFFFFF
                    c = 1 if tmp < rn else 0     # unsigned wrap = carry
                q = c if mm == q_new else (0 if c else 1)
                if q:
                    self.sr |= Q
                else:
                    self.sr &= ~Q
                T(1 if q == mm else 0)
                w(n, tmp)
                return None
            if sub == 0x5:      # DMULU.L -> MACH:MACL
                p = r(n) * r(m)
                self.macl = p & 0xFFFFFFFF
                self.mach = (p >> 32) & 0xFFFFFFFF
                return (None, False, 3)
            if sub == 0x6:      # CMP/HI (unsigned >)
                T(1 if r(n) > r(m) else 0)
                return None
            if sub == 0x7:      # CMP/GT (signed >)
                a = r(n) - 0x100000000 if r(n) & 0x80000000 else r(n)
                b = r(m) - 0x100000000 if r(m) & 0x80000000 else r(m)
                T(1 if a > b else 0)
                return None
            if sub == 0x8:      # SUB
                w(n, (r(n) - r(m)) & 0xFFFFFFFF)
                return None
            if sub == 0xA:      # SUBC (borrow -> T)
                tmp = r(n) - r(m) - (1 if self.t() else 0)
                T(1 if tmp < 0 else 0)
                w(n, tmp & 0xFFFFFFFF)
                return None
            if sub == 0xB:      # SUBV (overflow -> T)
                a = r(n) - 0x100000000 if r(n) & 0x80000000 else r(n)
                b = r(m) - 0x100000000 if r(m) & 0x80000000 else r(m)
                d = a - b
                T(1 if d < -0x80000000 or d > 0x7FFFFFFF else 0)
                w(n, (r(n) - r(m)) & 0xFFFFFFFF)
                return None
            if sub == 0xC:      # ADD
                w(n, (r(n) + r(m)) & 0xFFFFFFFF)
                return None
            if sub == 0xD:      # DMULS.L -> MACH:MACL
                a = r(n) - 0x100000000 if r(n) & 0x80000000 else r(n)
                b = r(m) - 0x100000000 if r(m) & 0x80000000 else r(m)
                p = (a * b) & 0xFFFFFFFFFFFFFFFF
                self.macl = p & 0xFFFFFFFF
                self.mach = (p >> 32) & 0xFFFFFFFF
                return (None, False, 3)
            if sub == 0xE:      # ADDC (carry -> T)
                tmp = r(n) + r(m) + (1 if self.t() else 0)
                T(1 if tmp > 0xFFFFFFFF else 0)
                w(n, tmp & 0xFFFFFFFF)
                return None
            if sub == 0xF:      # ADDV (overflow -> T)
                a = r(n) - 0x100000000 if r(n) & 0x80000000 else r(n)
                b = r(m) - 0x100000000 if r(m) & 0x80000000 else r(m)
                d = a + b
                T(1 if d < -0x80000000 or d > 0x7FFFFFFF else 0)
                w(n, (r(n) + r(m)) & 0xFFFFFFFF)
                return None
            raise SHException(0x100, 0x180)
        # ---- 0x4xxx: shifts, LDS/STS, JMP/JSR ----------------------------------
        if top == 0x4:
            lo = op & 0xFF
            rn = (op >> 8) & 0xF
            if lo == 0x00:      # SHLL Rn
                T(1 if r(rn) & 0x80000000 else 0)
                w(rn, (r(rn) << 1) & 0xFFFFFFFF)
                return None
            if lo == 0x01:      # SHLR Rn
                T(r(rn) & 1)
                w(rn, (r(rn) >> 1) & 0xFFFFFFFF)
                return None
            if lo == 0x21:  # SHAR Rn (arithmetic, 0100nnnn00100001)
                T(r(rn) & 1)
                v = r(rn) - 0x100000000 if r(rn) & 0x80000000 else r(rn)
                w(rn, (v >> 1) & 0xFFFFFFFF)
                return None
            if lo == 0x20:  # SHAL Rn (= SHLL)
                T(1 if r(rn) & 0x80000000 else 0)
                w(rn, (r(rn) << 1) & 0xFFFFFFFF)
                return None
            if lo == 0x04:      # ROTL Rn
                v = r(rn)
                T(1 if v & 0x80000000 else 0)
                w(rn, ((v << 1) | (v >> 31)) & 0xFFFFFFFF)
                return None
            if lo == 0x05:      # ROTR Rn
                v = r(rn)
                T(v & 1)
                w(rn, ((v >> 1) | ((v & 1) << 31)) & 0xFFFFFFFF)
                return None
            if lo == 0x06:      # LDS.L @Rm+,MACH
                self.mach = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x07:      # LDC.L @Rm+,SR (0100mmmm00000111)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                v = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                self.sr = v
                T(v & 1)
                return None
            if lo == 0x08:      # SHLL2
                w(rn, (r(rn) << 2) & 0xFFFFFFFF)
                return None
            if lo == 0x09:      # SHLR2
                w(rn, (r(rn) >> 2) & 0xFFFFFFFF)
                return None
            if lo == 0x0A:      # LDS Rm,MACH (m = bits 7:4)
                self.mach = r(n)
                return None
            if lo == 0x0B:      # JSR @Rn (PR = return past delay slot)
                self.pr = (nxt + 2) & 0xFFFFFFFF
                return (r(rn), True, 2)
            if lo == 0x0E:      # LDC Rm,SR (privileged, LSB->T)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                v = r(n)        # single read: storing SR may flip RB,
                self.sr = v     # which would change what a re-read returns
                T(v & 1)
                return None
            if lo == 0x10:      # DT Rn
                v = (r(rn) - 1) & 0xFFFFFFFF
                w(rn, v)
                T(1 if v == 0 else 0)
                return None
            if lo == 0x11:      # CMP/PZ Rn
                T(0 if r(rn) & 0x80000000 else 1)
                return None
            if lo == 0x15:      # CMP/PL Rn
                T(1 if r(rn) not in (0,) and not (r(rn) & 0x80000000) else 0)
                return None
            if lo == 0x16:      # LDS.L @Rm+,MACL
                self.macl = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x17:      # LDC.L @Rm+,GBR
                self.gbr = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x18:      # SHLL8
                w(rn, (r(rn) << 8) & 0xFFFFFFFF)
                return None
            if lo == 0x19:      # SHLR8
                w(rn, (r(rn) >> 8) & 0xFFFFFFFF)
                return None
            if lo == 0x1A:      # LDS Rm,MACL
                self.macl = r(n)
                return None
            if lo == 0x1B:      # TAS.B @Rn
                v = self.r8(r(rn))
                T(1 if v == 0 else 0)
                self.w8(r(rn), v | 0x80)
                return (None, False, 4)
            if lo == 0x1E:      # LDC Rm,GBR
                self.gbr = r(n)
                return None
            if lo == 0x24:      # ROTCL Rn
                v = r(rn)
                t = 1 if v & 0x80000000 else 0
                w(rn, ((v << 1) | (1 if self.t() else 0)) & 0xFFFFFFFF)
                T(t)
                return None
            if lo == 0x25:      # ROTCR Rn
                v = r(rn)
                t = v & 1
                w(rn, ((v >> 1) | ((1 if self.t() else 0) << 31)) & 0xFFFFFFFF)
                T(t)
                return None
            if lo == 0x26:      # LDS.L @Rm+,PR
                self.pr = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x27:      # LDC.L @Rm+,VBR
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.vbr = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x28:      # SHLL16
                w(rn, (r(rn) << 16) & 0xFFFFFFFF)
                return None
            if lo == 0x29:      # SHLR16
                w(rn, (r(rn) >> 16) & 0xFFFFFFFF)
                return None
            if lo == 0x2A:      # LDS Rm,PR
                self.pr = r(n)
                return None
            if lo == 0x2B:      # JMP @Rn
                return (r(rn), True, 2)
            if lo == 0x2E:      # LDC Rm,GBR? no: LDC Rm,VBR (0100mmmm00101110)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.vbr = r(n)
                return None
            if lo == 0x36:      # LDC.L @Rm+,SGR (0100mmmm00110110)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.sgr = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x37:      # LDC.L @Rm+,SSR
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.ssr = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x3A:      # LDC Rm,SGR
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.sgr = r(n)
                return None
            if lo == 0x3E:      # LDC Rm,SSR
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.ssr = r(n)
                return None
            if lo == 0x47:      # LDC.L @Rm+,SPC
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.spc = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x4E:      # LDC Rm,SPC
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                self.spc = r(n)
                return None
            if lo == 0x56:      # LDS.L @Rm+,FPUL
                self.fpul = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x5A:      # LDS Rm,FPUL
                self.fpul = r(n)
                return None
            if lo == 0x66:      # LDS.L @Rm+,FPSCR
                self.fpscr = self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            if lo == 0x6A:      # LDS Rm,FPSCR
                self.fpscr = r(n)
                return None
            if lo == 0xE9:      # MOVUA.L @Rm,R0 (unaligned)
                a = r(n)
                v = (self.mem.r8(self.translate(a)) |
                     (self.mem.r8(self.translate(a + 1)) << 8) |
                     (self.mem.r8(self.translate(a + 2)) << 16) |
                     (self.mem.r8(self.translate(a + 3)) << 24)) & 0xFFFFFFFF
                w(0, v)
                return None
            # SHAD/SHLD (manual Sec 10): only Rm[4:0] and the sign of Rm
            # matter. Rm>=0: Rn << Rm[4:0]; Rm<0: right by (~Rm[4:0])+1,
            # and Rm[4:0]==0 with Rm<0 means a full 32-bit shift.
            if (op & 0xF00F) == 0x400C:  # SHAD Rm,Rn (0100nnnnmmmm1100)
                s = r(m)
                v = r(n) - 0x100000000 if r(n) & 0x80000000 else r(n)
                if not s & 0x80000000:
                    w(n, (r(n) << (s & 0x1F)) & 0xFFFFFFFF)
                elif s & 0x1F == 0:
                    w(n, 0xFFFFFFFF if v < 0 else 0)
                else:
                    w(n, (v >> (((~s) & 0x1F) + 1)) & 0xFFFFFFFF)
                return None
            if (op & 0xF00F) == 0x400D:  # SHLD Rm,Rn (0100nnnnmmmm1101, logical)
                s = r(m)
                if not s & 0x80000000:
                    w(n, (r(n) << (s & 0x1F)) & 0xFFFFFFFF)
                elif s & 0x1F == 0:
                    w(n, 0)
                else:
                    w(n, r(n) >> (((~s) & 0x1F) + 1))
                return None
            if (op & 0xF00F) == 0x400F:  # MAC.W @Rm+,@Rn+ (0100nnnnmmmm1111)
                a = self._rd(r(m), 2)
                b = self._rd(r(n), 2)
                a = a - 0x10000 if a & 0x8000 else a
                b = b - 0x10000 if b & 0x8000 else b
                w(m, (r(m) + 2) & 0xFFFFFFFF)
                w(n, (r(n) + 2) & 0xFFFFFFFF)
                acc = ((self.mach << 32) | self.macl)
                acc = acc - 0x10000000000000000 if acc & 0x8000000000000000 else acc
                acc += a * b
                if self.sr & SBIT:  # saturation
                    if acc > 0x7FFFFFFFFFFF:
                        acc = 0x7FFFFFFFFFFF
                    elif acc < -0x800000000000:
                        acc = -0x800000000000
                acc &= 0xFFFFFFFFFFFFFFFF
                self.mach = (acc >> 32) & 0xFFFFFFFF
                self.macl = acc & 0xFFFFFFFF
                return (None, False, 3)
            if (op & 0xFF) in (0x02, 0x12, 0x22):  # STS.L MACH/MACL/PR,@-Rn
                w(n, (r(n) - 4) & 0xFFFFFFFF)
                v = {0x02: self.mach, 0x12: self.macl, 0x22: self.pr}[op & 0xFF]
                self._wr(r(n), 4, v)
                return None
            if (op & 0xFF) in (0x62, 0x52):  # STS.L FPSCR/FPUL,@-Rn
                w(n, (r(n) - 4) & 0xFFFFFFFF)
                self._wr(r(n), 4, self.fpscr if (op & 0xFF) == 0x62 else self.fpul)
                return None
            if (op & 0xF0FF) == 0x4013:  # STC.L GBR,@-Rn (0100nnnn00010011)
                w(n, (r(n) - 4) & 0xFFFFFFFF)
                self._wr(r(n), 4, self.gbr)
                return None
            # STC.L family (miasm/capstone-verified): SR/VBR/SSR/SPC/SGR
            # (no MD check, matching the existing STC handlers above)
            _stcl = {0x4003: self.sr, 0x4023: self.vbr, 0x4033: self.ssr,
                     0x4043: self.spc, 0x4032: self.sgr}
            if (op & 0xF0FF) in _stcl:  # STC.L sysreg,@-Rn
                w(n, (r(n) - 4) & 0xFFFFFFFF)
                self._wr(r(n), 4, _stcl[op & 0xF0FF])
                return None
            if (op & 0x008F) == 0x0083:  # STC.L Rm_BANK,@-Rn (0100nnnn1mmm0011)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                v = (self.b0 if self.sr & RB else self.b1)[(op >> 4) & 0x7]
                w(n, (r(n) - 4) & 0xFFFFFFFF)
                self._wr(r(n), 4, v)
                return None
            if (op & 0x008F) == 0x008E:  # LDC Rm,Rn_BANK (0100mmmm1nnn1110)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                # Rn_BANK = INACTIVE bank; mask excludes the Rm field
                (self.b0 if self.sr & RB else self.b1)[(op >> 4) & 0x7] = r(n)
                return None
            if (op & 0x008F) == 0x0087:
                # LDC.L @Rm+,Rn_BANK (0100mmmm1nnn0111); Rn_BANK = INACTIVE
                # bank, mask excludes Rm (was 0x0F8F: only @r0+ matched)
                if not (self.sr & MD):
                    raise SHException(0x100, 0x180)
                (self.b0 if self.sr & RB else self.b1)[(op >> 4) & 0x7] = \
                    self._rd(r(n), 4)
                w(n, (r(n) + 4) & 0xFFFFFFFF)
                return None
            raise SHException(0x100, 0x180)
        # ---- 0x6xxx: loads + extends -------------------------------------------
        if top == 0x6:
            sub = op & 0xF
            if sub == 0x0:      # MOV.B @Rm,Rn (sign-extended)
                w(n, _s8(self.r8(r(m))) & 0xFFFFFFFF)
                return None
            if sub == 0x1:      # MOV.W @Rm,Rn (sign-extended)
                v = self._rd(r(m), 2)
                w(n, (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
                return None
            if sub == 0x2:      # MOV.L @Rm,Rn
                w(n, self._rd(r(m), 4))
                return None
            if sub == 0x3:      # MOV Rm,Rn
                w(n, r(m))
                return None
            if sub == 0x4:      # MOV.B @Rm+,Rn
                w(n, _s8(self.r8(r(m))) & 0xFFFFFFFF)
                w(m, (r(m) + 1) & 0xFFFFFFFF)
                return None
            if sub == 0x5:      # MOV.W @Rm+,Rn
                v = self._rd(r(m), 2)
                w(n, (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
                w(m, (r(m) + 2) & 0xFFFFFFFF)
                return None
            if sub == 0x6:      # MOV.L @Rm+,Rn
                w(n, self._rd(r(m), 4))
                w(m, (r(m) + 4) & 0xFFFFFFFF)
                return None
            if sub == 0x7:      # NOT Rm,Rn
                w(n, (~r(m)) & 0xFFFFFFFF)
                return None
            if sub == 0x8:      # SWAP.B Rm,Rn (swap low two bytes)
                v = r(m)
                w(n, (v & 0xFFFF0000) | ((v & 0xFF) << 8) | ((v >> 8) & 0xFF))
                return None
            if sub == 0x9:      # SWAP.W Rm,Rn (swap halves)
                v = r(m)
                w(n, ((v >> 16) | (v << 16)) & 0xFFFFFFFF)
                return None
            if sub == 0xA:      # NEGC Rm,Rn (0 - Rm - T)
                tmp = 0 - r(m) - (1 if self.t() else 0)
                T(1 if tmp < 0 else 0)
                w(n, tmp & 0xFFFFFFFF)
                return None
            if sub == 0xB:      # NEG Rm,Rn
                w(n, (0 - r(m)) & 0xFFFFFFFF)
                return None
            if sub == 0xC:      # EXTU.B Rm,Rn
                w(n, r(m) & 0xFF)
                return None
            if sub == 0xD:      # EXTU.W Rm,Rn
                w(n, r(m) & 0xFFFF)
                return None
            if sub == 0xE:      # EXTS.B Rm,Rn
                w(n, _s8(r(m) & 0xFF) & 0xFFFFFFFF)
                return None
            if sub == 0xF:      # EXTS.W Rm,Rn
                v = r(m) & 0xFFFF
                w(n, (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
                return None
            raise SHException(0x100, 0x180)
        # ---- 0x7xxx: ADD #imm ----------------------------------------------------
        if top == 0x7:
            w(n, (r(n) + _s8(i8)) & 0xFFFFFFFF)
            return None
        # ---- 0x8xxx: disp moves + CMP/EQ# + BT/BF ----------------------------------------
        if top == 0x8:
            sub = (op >> 8) & 0xF
            if sub == 0x0:      # MOV.B R0,@(disp,Rn)
                self.w8((r(m) + (op & 0xF)) & 0xFFFFFFFF, r(0) & 0xFF)
                return None
            if sub == 0x1:      # MOV.W R0,@(disp,Rn)
                self._wr((r(m) + (op & 0xF) * 2) & 0xFFFFFFFF, 2, r(0))
                return None
            if sub == 0x4:      # MOV.B @(disp,Rm),R0
                w(0, _s8(self.r8((r(m) + (op & 0xF)) & 0xFFFFFFFF)) & 0xFFFFFFFF)
                return None
            if sub == 0x5:      # MOV.W @(disp,Rm),R0
                v = self._rd((r(m) + (op & 0xF) * 2) & 0xFFFFFFFF, 2)
                w(0, (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
                return None
            if (op & 0xFF00) == 0x8800:  # CMP/EQ #imm,R0 (imm sign-extended)
                T(1 if r(0) == (_s8(i8) & 0xFFFFFFFF) else 0)
                return None
            disp = _s8(i8) * 2 + 4
            if (op & 0xFF00) == 0x8900:  # BT
                return ((self.pc + disp) & 0xFFFFFFFF, False, 1) if self.t() else None
            if (op & 0xFF00) == 0x8B00:  # BF
                return None if self.t() else ((self.pc + disp) & 0xFFFFFFFF, False, 1)
            if (op & 0xFF00) == 0x8D00:  # BT/S
                return ((self.pc + disp) & 0xFFFFFFFF, True, 1) if self.t() else None
            if (op & 0xFF00) == 0x8F00:  # BF/S
                return None if self.t() else ((self.pc + disp) & 0xFFFFFFFF, True, 1)
            raise SHException(0x100, 0x180)
        # ---- 0x9xxx: MOV.W @(disp,PC) ----------------------------------------------
        if top == 0x9:
            # MOV.W uses PC+4 unaligned (only MOV.L/MOVA round PC down to
            # a longword). The old (&~3) read 2 bytes early whenever the
            # instruction sat at pc%4==2 -- e.g. frame sizes came out wrong.
            v = self._rd((self.pc + 4 + i8 * 2) & 0xFFFFFFFF, 2)
            w(n, (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
            return None
        # ---- 0xAxxx/0xBxxx: BRA/BSR ---------------------------------------------------
        if top == 0xA:
            d = op & 0xFFF
            if d & 0x800:
                d -= 0x1000
            return ((self.pc + 4 + d * 2) & 0xFFFFFFFF, True, 2)
        if top == 0xB:
            d = op & 0xFFF
            if d & 0x800:
                d -= 0x1000
            self.pr = (nxt + 2) & 0xFFFFFFFF  # return past delay slot
            return ((self.pc + 4 + d * 2) & 0xFFFFFFFF, True, 2)
        # ---- 0xCxxx: MOVA/TRAPA/GBR moves -----------------------------------------------
        if top == 0xC:
            hi = op & 0xFF00
            if hi == 0xC700:    # MOVA @(disp,PC),R0
                w(0, ((self.pc + 4) & ~3) + (op & 0xFF) * 4)
                return None
            if hi == 0xC300:    # TRAPA #imm
                self.tra = (i8 << 2) & 0xFFFFFFFF
                pc_now = self.pc
                self.enter_exc(0x100, 0x160, (pc_now + 2) & 0xFFFFFFFF)
                return ("exc", False, 8)
            if hi == 0xC000:    # MOV.B R0,@(disp,GBR)
                self.w8((self.gbr + (op & 0xFF)) & 0xFFFFFFFF, r(0) & 0xFF)
                return None
            if hi == 0xC100:    # MOV.W R0,@(disp,GBR)
                self._wr((self.gbr + (op & 0xFF) * 2) & 0xFFFFFFFF, 2, r(0))
                return None
            if hi == 0xC200:    # MOV.L R0,@(disp,GBR)
                self._wr((self.gbr + (op & 0xFF) * 4) & 0xFFFFFFFF, 4, r(0))
                return None
            if hi == 0xC400:    # MOV.B @(disp,GBR),R0
                w(0, _s8(self.r8((self.gbr + (op & 0xFF)) & 0xFFFFFFFF)) & 0xFFFFFFFF)
                return None
            if hi == 0xC500:    # MOV.W @(disp,GBR),R0
                v = self._rd((self.gbr + (op & 0xFF) * 2) & 0xFFFFFFFF, 2)
                w(0, (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
                return None
            if hi == 0xC600:    # MOV.L @(disp,GBR),R0
                w(0, self._rd((self.gbr + (op & 0xFF) * 4) & 0xFFFFFFFF, 4))
                return None
            if hi == 0xC800:    # TST #imm,R0
                T(1 if (r(0) & i8) == 0 else 0)
                return None
            if hi == 0xC900:    # AND #imm,R0
                w(0, r(0) & i8)
                return None
            if hi == 0xCA00:    # XOR #imm,R0
                w(0, r(0) ^ i8)
                return None
            if hi == 0xCB00:    # OR #imm,R0
                w(0, r(0) | i8)
                return None
            if hi == 0xCC00:    # TST.B #imm,@(R0,GBR)
                T(1 if (self.r8((r(0) + self.gbr) & 0xFFFFFFFF) & i8) == 0 else 0)
                return None
            if hi == 0xCD00:    # AND.B #imm,@(R0,GBR)
                a = (r(0) + self.gbr) & 0xFFFFFFFF
                self.w8(a, self.r8(a) & i8)
                return None
            if hi == 0xCE00:    # XOR.B #imm,@(R0,GBR)
                a = (r(0) + self.gbr) & 0xFFFFFFFF
                self.w8(a, self.r8(a) ^ i8)
                return None
            if hi == 0xCF00:    # OR.B #imm,@(R0,GBR)
                a = (r(0) + self.gbr) & 0xFFFFFFFF
                self.w8(a, self.r8(a) | i8)
                return None
            raise SHException(0x100, 0x180)
        # ---- 0xDxxx: MOV.L @(disp,PC) ----------------------------------------------------
        if top == 0xD:
            w(n, self._rd(((self.pc + 4) & ~3) + i8 * 4, 4))
            return None
        # ---- 0xExxx: MOV #imm ---------------------------------------------------------------
        if top == 0xE:
            w(n, _s8(i8) & 0xFFFFFFFF)
            return None
        raise SHException(0x100, 0x180)
