"""cpu.py -- C674x DSP CPU for the XDJ-700 emulator (v2 engine).

Replaces core.py's engine (2026-10-11 review: SPLOOP capture was a no-op,
compact packets executed as one group, branch targets rounded down to the
fetch packet, an extra cycle on every branch landing, EXT/CLR/SET maths
wrong). Instruction semantics live in isa.py; this file is the machine.

Execution model (TI SPRUFE8B ch.3-4):
  * An execute packet (EP) issues in one cycle i (its E1). Members are the
    instructions from pc onward chained by the parallel flag (objdump's
    "||" = previous insn's p-bit). Header words of compact fetch packets
    are skipped; EPs may start mid fetch packet (branch targets).
  * Results scheduled with "write at cycle w" commit at the END of cycle
    w (readable from cycle w+1). Single-cycle w=i, 16x16 MPY i+1, 4-cycle
    i+3, loads i+4 (memory read at the end of i+3 = E4), stores reach
    memory at the end of i+2 (E3), stores before loads in the same cycle.
  * Branches: target EP issues at cycle i+6 (5 delay slots); the pc is
    redirected at the end of cycle i+5. A multi-cycle NOP that is still
    running when a branch lands is cut short.
  * All members of an EP read registers before any of them write.

Memory is any object with read(addr, size) -> int and write(addr, size,
val). Program text comes from decode.decode_bytes(); call add_code() for
every region that will execute (code loaded at runtime -> add_code again).
"""
from __future__ import annotations

import collections
import re

from . import isa

MASK = 0xFFFFFFFF

REGNAMES = [f"a{i}" for i in range(32)] + [f"b{i}" for i in range(32)]
REGIDX = {n: i for i, n in enumerate(REGNAMES)}
CTRL = ("amr csr ifr isr icr ier istp irp nrp ilc rilc ssr tsr itsr ntsr ecr "
        "efr ierr rep tscl tsch dnum gfpgfr gplya gplyb fadcr faucr fmcr dier "
        "pce1 dier").split()


class Halt(Exception):
    """Unimplemented instruction / bad state: stops the run with context."""


class Cpu:
    def __init__(self, mem):
        self.mem = mem
        self.r = [0] * 64
        self.c = {n: 0 for n in CTRL}
        self.c["csr"] = 0x14000100       # C674x CPU ID/rev (CSR reset value)
        self.prog = {}                  # addr -> decode.Insn
        self.ops = {}                   # addr -> compiled callable
        self.pc = 0
        self.cycle = 0
        self.ev = collections.defaultdict(list)   # cycle -> [events]
        self.pend = {}                  # landing cycle -> target (several
                                        # branches may be in flight)
        self.stall = 0                  # extra cycles of a multi-cycle NOP
        self.ep_count = 0
        self.insn_count = 0
        self.unknown = collections.Counter()
        self.trace = None               # callable(cpu, pc, ep) or None
        self.sp = None                  # SPLOOP state (isa.SPLoop) or None
        self.idle = False
        self.splx_irq = None            # SPLOOP drained for an interrupt:
                                        # take it once writes complete

    # ------------------------------------------------------------ program
    def add_code(self, insns):
        self.prog.update(insns)
        for a in insns:
            self.ops.pop(a, None)

    def _next_addr(self, a):
        i = self.prog.get(a)
        n = a + (i.size if i else 4)
        h = self.prog.get(n)
        while h is not None and h.header:
            n += 4
            h = self.prog.get(n)
        return n

    def execute_packet(self, pc):
        """[Insn...] of the EP starting at pc, and the next sequential pc."""
        i = self.prog.get(pc)
        if i is None:
            raise Halt(f"no code at {pc:#010x}")
        if i.header:
            pc = self._next_addr(pc - 4 + 0) if False else pc + 4
            i = self.prog.get(pc)
        ep = [i]
        a = pc
        while True:
            n = self._next_addr(a)
            j = self.prog.get(n)
            if j is None or not j.par or j.header:
                return ep, n
            ep.append(j)
            a = n

    # ------------------------------------------------------------ timing
    def wr(self, w, idx, val):
        """register idx gets val at the end of cycle w"""
        self.ev[w].append((1, idx, val & MASK))

    def wr_ctrl(self, w, name, val):
        self.ev[w].append((2, name, val & MASK))

    def st(self, w, addr, size, val):
        self.ev[w].append((0, addr & MASK, size, val))

    def ld(self, issue, addr, size, fn):
        """memory read at the end of issue+3, fn(value) at end of issue+3
        schedules the register write for issue+4 (it gets the cpu)."""
        self.ev[issue + 3].append((3, addr & MASK, size, fn))

    @property
    def branch_at(self):
        """earliest pending branch (cycle, target) or None"""
        if not self.pend:
            return None
        c = min(self.pend)
        return (c, self.pend[c])

    def branch(self, issue, target):
        # A branch issued in another branch's delay slots is legal C6000
        # code (Pioneer's DSP image calls through one: bnop L; [a0] b fn
        # two cycles later). Each lands at its own cycle, in order. The
        # old single slot let the second overwrite the first, so the core
        # ran the fall-through code (mvk 0,a11 -> NULL table pointer).
        self.pend[issue + 5] = target & MASK

    def _commit(self, cyc):
        evs = self.ev.pop(cyc, None)
        if not evs:
            return
        # stores first, then loads (reads), then register/control writes
        for e in evs:
            if e[0] == 0:
                self.mem.write(e[1], e[2], e[3])
        for e in evs:
            if e[0] == 3:
                v = self.mem.read(e[1], e[2])
                e[3](self, v)          # schedules the register write(s)
        late = self.ev.pop(cyc, None)  # writes scheduled by load callbacks
        for e in evs + (late or []):
            if e[0] == 1:
                self.r[e[1]] = e[2]
            elif e[0] == 2:
                self.c[e[1]] = e[2]
                if e[1] in ("csr", "tsr"):   # C64x+: CSR.GIE and TSR.GIE are one bit
                    other = "tsr" if e[1] == "csr" else "csr"
                    self.c[other] = (self.c[other] & ~1) | (e[2] & 1)

    # ------------------------------------------------------------ run
    def take_interrupt(self):
        """C674x maskable interrupt INT4..15 at an execute-packet boundary:
        needs CSR.GIE, IER.NMIE, IER bit and IFR bit; never with a branch
        in flight, inside SPLOOP or during a multi-cycle NOP. Entry: IRP =
        next packet, PGIE = GIE, GIE = 0, ITSR = TSR, IFR bit cleared,
        pc = ISTP.ISTB + 32 * n (SPRUFE8B ch.5)."""
        c = self.c
        if not (c["csr"] & 1) or not (c["ier"] & 2) or self.pend or \
                self.stall or (self.sp is not None) or self.ev:
            return False
        pend = c["ifr"] & c["ier"] & 0xFFF0
        if not pend:
            return False
        n = (pend & -pend).bit_length() - 1          # lowest number first
        c["ifr"] &= ~(1 << n)
        c["irp"] = self.pc
        c["csr"] = (c["csr"] & ~3) | ((c["csr"] & 1) << 1)   # PGIE <- GIE, GIE <- 0
        c["itsr"] = c["tsr"]
        c["tsr"] &= ~1
        self.pc = (c["istp"] & ~0x3FF) + 32 * n
        self.interrupts = getattr(self, "interrupts", 0) + 1
        return True

    def step(self):
        """advance one cycle"""
        cyc = self.cycle
        if self.splx_irq is not None:
            # §7.13.4 steps 7-8: complete pending writes, then the interrupt
            if not self.ev and not self.pend:
                after = self.splx_irq
                self.splx_irq = None
                if self.take_interrupt():
                    self.c["itsr"] |= isa.SPLX
                    if getattr(self, "code_hook", None):
                        self.code_hook(self.pc)
                elif after is not None:
                    self.pc = after             # §7.13.6: interrupt went away
            else:
                self._commit(cyc)
                self.cycle = cyc + 1
                return
        if not self.stall and self.sp is None and (self.c["ifr"] & self.c["ier"] & 0xFFF0):
            if self.take_interrupt() and getattr(self, "code_hook", None):
                self.code_hook(self.pc)      # vector code may not be decoded yet
        if self.stall:
            self.stall -= 1
        elif self.sp is not None and self.sp.active:
            self.sp.cycle(self, cyc)
        else:
            ep, nxt = self.execute_packet(self.pc)
            self.issue(ep, cyc)
            self.pc = nxt
        self._commit(cyc)
        if cyc in self.pend:
            self.pc = self.pend.pop(cyc)
            self.stall = 0                 # a landing branch ends a NOP
            if self.sp is not None:
                self.sp.on_branch(self)
        self.cycle = cyc + 1

    def issue(self, ep, cyc):
        """issue an execute packet at cycle cyc (all reads before writes:
        every handler only READS self.r and schedules writes)."""
        self.ep_count += 1
        if self.trace:
            self.trace(self, ep[0].addr, ep)
        nop = 1
        if ep[0].mnem.startswith("sploop") and self.c["tsr"] & isa.SPLX:
            ep = ep[:1]                         # §7.13.2: only the SPLOOP runs
        for i in ep:
            f = self.ops.get(i.addr)
            if f is None:
                f = self.ops[i.addr] = isa.compile_insn(self, i)
            n = f(self, cyc)
            self.insn_count += 1
            if n and n > nop:
                nop = n
        if nop > 1:
            self.stall = nop - 1

    def run(self, cycles, stop=None):
        for _ in range(cycles):
            self.step()
            if stop is not None and stop(self):
                return True
        return False

    def cond(self, c):
        """predicate (reg, negate) -> bool, read at issue"""
        if c is None:
            return True
        v = self.r[REGIDX[c[0]]]
        return (v == 0) if c[1] else (v != 0)
