"""core.py -- Python C674x CPU core for the XDJ-700 DSP (TI OMAP-L1x/C6747).

Continues CLAUDE.md §64/§67: the SH loads stage-1 + the 390 KB main image
through the HPI; this core runs the captured stage-1 standalone first.

Timing model (SPRUFE8B §4.2): an instruction in E1 at cycle c commits at the
END of cycle c+delta (visible to E1 of c+delta+1):
  single-cycle 0 | MPY 16x16 1 | stores (E3 mem) 2 | extended MPY/4-cyc FP 3
  loads 4 | branches: target begins at packet c+6 (5 delay slots)
  MPYI 9 | MPYID 10 | MPYDP 10 | ADDDP/SUBDP lo 6 hi 7 | DP compare 2
  two-cycle DP (ABSDP/SPDP/DPSP*...) lo 1 hi 2 (SPDP: lo c+1, hi c+2)
  SPLOOPW termination reads a condition snapshot 3 cycles old.

Branch mechanics (§3.4/4.2.6): the packet containing the branch and the 5
following packets (delay slots) execute normally; PC changes for the 6th
packet. We implement it as a scheduled pc set at cycle c+5 (i.e. end of
cycle c+5 = before packet c+6) using the *packet index* as the cycle unit
for scheduling -- one packet = one E1 cycle in this model.

Events queue: writes <= current E1 cycle commit before operand read.
"""

from __future__ import annotations

import math
import re
import struct

MASK32 = 0xFFFFFFFF

_CPU_NAMES = ("A0 A1 A2 A3 A4 A5 A6 A7 A8 A9 A10 A11 A12 A13 A14 A15 "
              "A16 A17 A18 A19 A20 A21 A22 A23 A24 A25 A26 A27 A28 A29 A30 A31 "
              "B0 B1 B2 B3 B4 B5 B6 B7 B8 B9 B10 B11 B12 B13 B14 B15 "
              "B16 B17 B18 B19 B20 B21 B22 B23 B24 B25 B26 B27 B28 B29 B30 B31").split()
_REGID = {n: i for i, n in enumerate(_CPU_NAMES)}

CTRL_NAMES = ("AMR CSR IFR IER ISTP IRP NRP ICR ISR TSR ITSR NTSR ILC RILC "
              "DIER REP SSR PCE1 DNUM FAUCR FADCR TSCL TSCH GFPGFR FMCR").split()


def reg_id(name):
    s = name.strip().upper()
    if ":" in s:                      # pair form a13:a12 / a13:A12 -> low reg
        lo = s.split(":")[0].strip()
        return _REGID[lo]
    try:
        return _REGID[s]
    except KeyError:
        raise KeyError(f"unknown register {name!r}")


def _u32(x):
    return x & MASK32


def _s32(x):
    x &= MASK32
    return x - (1 << 32) if x & 0x80000000 else x


def _s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def _u16(x):
    return x & 0xFFFF


def _sext(v, bits):
    s = 1 << (bits - 1)
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & s else v


def _f32(x):
    """Round a Python float to IEEE-754 single precision."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except (OverflowError, ValueError):
        return float("inf") if x > 0 else float("-inf")


def _dp(lo, hi):
    """Two 32-bit halves -> Python double (TI DP layout: hi/lo words)."""
    return struct.unpack("<d", struct.pack("<II", lo, hi))[0]


def _split_dp(v):
    return struct.unpack("<II", struct.pack("<d", v))


def _sat_s32(x):
    if x > 0x7FFFFFFF:
        return 0x7FFFFFFF
    if x < -0x80000000:
        return -0x80000000
    return x


# memory --------------------------------------------------------------

class DSPMem:
    """L2 256K @0x11800000 (alias 0x00800000), DDR window, MMIO hooks.

    hooks: {page>>12: fn(addr, size, kind, val) -> value} for reads; on
    writes the hook is called and sanctioned afterwards.
    """

    L2_BASE = 0x11800000
    L2_ALIAS = 0x00800000
    L2_SIZE = 256 * 1024
    DDR_BASE = 0xC0000000

    def __init__(self, ddr_size=8 * 1024 * 1024, hooks=None):
        self.l2 = bytearray(self.L2_SIZE)
        self.ddr = bytearray(ddr_size)
        self.hooks = hooks or {}
        self.mmio_log = []

    def _locate(self, addr):
        if self.L2_BASE <= addr < self.L2_BASE + self.L2_SIZE:
            return self.l2, addr - self.L2_BASE
        if self.L2_ALIAS <= addr < self.L2_ALIAS + self.L2_SIZE:
            return self.l2, addr - self.L2_ALIAS
        if self.DDR_BASE <= addr < self.DDR_BASE + len(self.ddr):
            return self.ddr, addr - self.DDR_BASE
        return None, None

    def read(self, addr, size):
        addr &= MASK32
        ba, off = self._locate(addr)
        if ba is not None:
            return int.from_bytes(ba[off:off + size], "little")
        hook = self.hooks.get(addr >> 12)
        v = hook(addr, size, "r", 0) if hook else 0
        self.mmio_log.append((addr, size, "r", v))
        return v

    def write(self, addr, size, val):
        addr &= MASK32
        val &= (1 << (size * 8)) - 1
        ba, off = self._locate(addr)
        if ba is not None:
            ba[off:off + size] = val.to_bytes(size, "little")
            return
        hook = self.hooks.get(addr >> 12)
        if hook:
            hook(addr, size, "w", val)
        self.mmio_log.append((addr, size, "w", val))


# SPLOOP --------------------------------------------------------------

class SPState:
    """Loop buffer (SPRUFE8B §7). mode: 0 load, 1 kernel, 2 drain."""

    __slots__ = ("active", "mode", "ii", "body", "valid", "wcond", "neg",
                 "spk_f", "pc_after", "masked", "ntri", "start_cycle",
                 "body_len", "kind", "init_term_done")

    def __init__(self):
        self.active = False
        self.mode = 0
        self.ii = 1
        self.body = []      # list of insns (ordered); body_len cycles
        self.valid = []
        self.wcond = None   # (regidx, negate) for SPLOOPW
        self.kind = ""      # 'sploop'|'sploopd'|'sploopw'
        self.pc_after = 0

    def clear(self):
        self.__init__()


class Core:
    def __init__(self, mem=None):
        self.mem = mem if mem is not None else DSPMem()
        self.r = [0] * 64
        self.ctl = {k: 0 for k in CTRL_NAMES}
        self.insns = {}
        self.raw = {}                          # addr -> 32-bit raw word
        self.pc = 0x11800000
        self.cycle = 0
        self.exec_insns = 0
        self.packets = 0
        self.trace = []
        self.tracing = False
        self.stopped = False
        self.stop_reason = None
        self.stop_pc = None
        self.wq = []        # (due_cycle_end, regidx, val): commit if due <= c
        self.cwq = []       # control regs
        self.mq = []        # (due_end_cycle, addr, size, val)
        self.brq = []       # (due_end_cycle, target)
        self.iq = []        # ILC writes: (due_end_cycle, val)
        self.sp = SPState()
        self.cur_text = ""
        self.mmio_reader = None

    # ------------------------------------------------------------- regions
    def add_code_region(self, blob, vma):
        from .decode import decode_bytes
        self.insns.update(decode_bytes(blob, vma))
        for a, b in zip(range(vma, vma + len(blob) // 4 * 4, 4),
                        range(0, len(blob) - 3, 4)):
            self.raw[a] = struct.unpack_from("<I", blob, b)[0]

    def log(self, s):
        if self.tracing:
            self.trace.append((self.cycle, self.pc, s))
            if len(self.trace) > 20000:
                self.trace = self.trace[-10000:]

    # ------------------------------------------------------------- run
    def run(self, n=1):
        for _ in range(n):
            if self.stopped:
                return False
            self.step_cycle()
        return not self.stopped

    def run_to(self, stop_fn, max_cycles=50_000_000):
        for _ in range(max_cycles):
            if self.stopped:
                return False
            self.step_cycle()
            if stop_fn(self):
                return True
        return False

    def stop(self, reason, pc=None):
        self.stopped = True
        self.stop_reason = reason
        self.stop_pc = self.pc if pc is None else pc

    # ------------------------------------------------------------- timing
    def write_reg(self, i, val, delta):
        self.wq.append((self.cycle + delta, i, _u32(val)))

    def write_ctrl(self, name, val, delta):
        self.cwq.append((self.cycle + delta, name, _u32(val)))

    def write_mem(self, addr, size, val, delta=2):
        self.mq.append((self.cycle + delta, addr & MASK32, size, _u32(val)))

    def branch(self, target, delta=5):
        # branch target starts executing in the packet 5 packets after E1
        self.brq.append((self.cycle + delta, target & ~31))

    def read_reg(self, name):
        return self.r[reg_id(name)] if isinstance(name, str) else self.r[name]

    def rd(self, idx):
        return self.r[idx]

    def RI(self, name):
        if isinstance(name, int):
            return name
        try:
            return reg_id(name)
        except KeyError:
            raise KeyError(f"register {name!r} at cycle {self.cycle} insn={getattr(self, 'cur_text', '?')}")

    def _cond_true(self, cond):
        if not cond:
            return True
        name, neg = cond
        v = self.r[reg_id(name)]
        t = (v & 1) == 1        # creg tests bit 0 for B0/B1/B2 (A0..A2 too)
        return t if not neg else not t

    # ------------------------------------------------------------- cycle
    def step_cycle(self):
        c = self.cycle
        self.cycle = c + 1
        # commit side effects due at end of cycle c
        if self.wq:
            nxt = []
            for due, i, v in self.wq:
                if due <= c:
                    self.r[i] = v
                else:
                    nxt.append((due, i, v))
            self.wq = nxt
        if self.cwq:
            nxt = []
            for due, name, v in self.cwq:
                if due <= c:
                    self.ctl[name] = v
                else:
                    nxt.append((due, name, v))
            self.cwq = nxt
        if self.mq:
            nxt = []
            for due, addr, size, v in self.mq:
                if due <= c:
                    self.mem.write(addr, size, v)
                else:
                    nxt.append((due, addr, size, v))
            self.mq = nxt
        if self.brq:
            nxt = []
            landed = False
            for due, tgt in self.brq:
                if due <= c:
                    self.pc = tgt
                    landed = True
                else:
                    nxt.append((due, tgt))
            self.brq = nxt
            if landed:
                return          # target executes next cycle
        if self.iq:
            nxt = []
            for due, v in self.iq:
                if due <= c:
                    self.ctl["ILC"] = v
                else:
                    nxt.append((due, v))
            self.iq = nxt

        if self.sp.active:
            self._sp_cycle()
            return
        self._exec_packet(self.pc)
        if not self.stopped:
            self.pc = self.ep_next

    # ------------------------------------------------------------- packets
    def _exec_packet(self, pc):
        ins = self.insns
        i = ins.get(pc)
        if i is None:
            self.stop("no-insn", pc)
            return
        if i.header:
            # header-based (compact) fetch packet: walk the remaining words
            # of this 32-byte packet (excluding the header) as the group.
            base = pc & ~31
            group = []
            for a in range(pc + 4, base + 32):
                t = ins.get(a)
                if t and t.mnem not in ("header", "undefined"):
                    group.append(t)
            self.ep_size = (pc + 28) - (pc & ~31) if False else (base + 32) - pc
            self.ep_next = base + 32
            self.exec_insns += len(group)
            self.packets += 1
            self.cur_text = group[0].text if group else "<hdr-only>"
            if self.tracing and group:
                self.trace.append((self.cycle, pc,
                                   " || ".join(t.text for t in group)))
            for t in group:
                self.EXEC(t)
            return
        group = [i]
        # C6x semantics: instruction N's p-bit (shown as "||" on N+1 in
        # objdump) decides whether N+1 is in the same execute packet, so
        # the chain is walked via the PAR flags of the FOLLOWING insns.
        last = i
        while True:
            nxt_addr = last.addr + last.size
            nxt = ins.get(nxt_addr)
            if nxt is None or not nxt.par or (nxt_addr & 31) == 0:
                break
            group.append(nxt)
            last = nxt
            if nxt_addr & 31 == 0:
                # parallel chain would cross the fetch packet boundary:
                # invalid by C6x encoding rules; treat as decode artefact
                self.stop("par-cross-flash", nxt_addr)
                return
            nxt = ins.get(nxt_addr)
            if nxt is None:
                self.stop("par-overrun", nxt_addr)
                return
            group.append(nxt)
            last = nxt
        self.ep_size = last.addr + last.size - pc
        # next exec packet starts at the chain terminator (used for
        # fall-through advance regardless of where pc started)
        self.ep_next = last.addr + last.size
        self.exec_insns += len(group)
        self.packets += 1
        self.cur_text = group[0].text
        if self.tracing:
            gtxt = " || ".join(t.text for t in group)
            self.trace.append((self.cycle, pc, gtxt))
            if len(self.trace) > 30000:
                self.trace = self.trace[-15000:]
        for t in group:
            if t.mnem in ("undefined", "header"):
                # header at packet start during normal exec = a compact
                # packet reached by fall-through; dispatch it
                if t.mnem == "header" and (t.addr & 31) == 0:
                    self._exec_compact(t.addr)
                    if self.stopped:
                        return
                    continue
                self.stop("undefined-insn", t.addr)
                return
            self.EXEC(t)

    # ------------------------------------------------------- compact packet
    def _exec_compact(self, pc):
        base = pc & ~31
        h = self.raw.get(base)
        if h is None:
            self.stop("no-header", pc)
            return
        layout = (h >> 21) & 0x7F
        # decode.py already split the whole region into Insn records with
        # correct compact texts (incl. the header at base). Collect words
        # 1..7 in packet order with their parallel bits.
        group = []
        for k in range(7):
            a = base + 4 + k * 4
            if (layout >> k) & 1:
                for half in (a, a + 2):
                    i1 = self.insns.get(half)
                    if i1 is not None:
                        group.append(i1)
            else:
                i1 = self.insns.get(a)
                if i1 is not None:
                    group.append(i1)
        self.ep_size = 32 - (pc - base)
        self.exec_insns += len(group)
        self.packets += 1
        self.cur_text = group[0].text if group else "<empty-packet>"
        if self.tracing:
            gtxt = " || ".join(t.text for t in group)
            self.trace.append((self.cycle, pc, gtxt))
            if len(self.trace) > 30000:
                self.trace = self.trace[-15000:]
        for t in group:
            if t.mnem in ("undefined", "header"):
                # header at packet start during normal exec = a compact
                # packet reached by fall-through; dispatch it
                if t.mnem == "header" and (t.addr & 31) == 0:
                    self._exec_compact(t.addr)
                    if self.stopped:
                        return
                    continue
                self.stop("undefined-insn", t.addr)
                return
            self.EXEC(t)

    def _decode_one(self, raww, addr):
        return None

# ---------------------------------------------------------------------
# compact execution: build Insn objects for the 16-bit halves lazily by
# ======================================================================
# instruction execution (EXEC dispatch) + SPLOOP engine
# ======================================================================

_RE_MVKH = re.compile(r"^0x?([0-9a-fA-F]+)$")


def _intop(s):
    """0x1f / 123 / -4 -> int; None for register names."""
    try:
        return int(s, 0)
    except (ValueError, TypeError):
        if s.startswith("-") and s[1:].isdigit():
            return int(s, 0)
        return None


class _BadOp(ValueError):
    pass


# ---------------------------------------------------------------------------
# EXEC: full instruction dispatch
# ---------------------------------------------------------------------------

IS_CTRL = {"AMR", "CSR", "IFR", "IER", "ISTP", "IRP", "NRP", "ICR", "ISR",
           "TSR", "ITSR", "NTSR", "ILC", "RILC", "DIER", "REP", "SSR",
           "PCE1", "DNUM", "FAUCR", "FADCR", "TSCL", "TSCH", "GFPGFR", "FMCR"}

IS_CTRL_LOW = {n.lower() for n in IS_CTRL}


def _operand(self, s):
    """Operand -> (kind, value): kind REG/IMM. Registers include pairs."""
    v = _intop(s)
    if v is not None:
        return ("IMM", v)
    s2 = s.strip()
    if s2.lower() in ("ilc", "rilc", "irp", "nrp", "csr", "amr", "tsr",
                      "itsr", "ntsr", "fadcr", "faucr", "fmcr", "ier",
                      "ifr", "isr", "icr", "istp", "ierr", "tscl", "tsch"):
        return ("CTRL", s2.upper())
    return ("REG", reg_id(s2))


def _val(self, s):
    k, v = _operand(self, s)
    if k == "REG":
        return self.r[v]
    if k == "CTRL":
        return self.ctl[v]
    return v & MASK32


def _dst_write(self, dst, val, delta):
    """dst reg or control register text -> schedule write."""
    k, v = _operand(self, dst)
    if k == "REG":
        self.write_reg(v, val, delta)
    else:
        self.write_ctrl(v, val, delta)


def _addr_of(self, op):
    """ldst operand -> (addr, post_delta or None)."""
    s = op.strip()
    assert s.startswith("*"), op
    body = s[1:]
    neg = False
    if body and body[0] in "+-":
        neg = body[0] == "-"
        body = body[1:]
    # forms: R(d)  R[Aidx]  ++R(d) --R(d) R++(d) R--(d) R  R++
    pre = ""
    if body[:2] in ("++", "--"):
        pre = body[:2]
        body = body[2:]
    post = None
    if body.endswith("(1)") is False and (body.endswith("(2)") or body.endswith("(4)") or body.endswith(")") is False):
        pass
    # general "(...)" displacement
    if "(" in body:
        reg, rest = body.split("(", 1)
        d = rest[:-1]
    elif "[" in body and body.endswith("]"):
        reg, rest = body.split("[", 1)
        d = "*" + rest[:-1]     # scaled index marker
    else:
        reg, d = body, None
    reg = reg.strip().lstrip("+").lstrip("-")
    if reg.endswith("++") or reg.endswith("--"):
        reg = reg[:-2]
    # post increment/decrement: trailing ++/-- on the full operand
    postinc = False
    full = s[1:]
    if full.endswith("++"):
        postinc = "+"
    elif full.endswith("--"):
        postinc = "-"
    base = self.r[reg_id(reg)]
    mult = 4 if ("(" in body and reg[0] == "a" or reg[0] == "b") else 1
    # scale by access size is NOT applied to raw displacements in C6x:
    # displacements are already scaled per size in the encoding; objdump
    # prints the raw scaled value, so use it directly.
    if d is None:
        disp = 0
    elif d.startswith("*"):
        dx = self.r[reg_id(d[1:])]
        disp = dx * 4              # *Aidx scaled indexes are word-scaled
    else:
        disp = int(d, 0)
    if neg:
        disp = -disp
    addr = (base + disp) & MASK32
    post = None
    if postinc:
        dv = disp if disp else 0
        post = dv
    return addr, (post, base, reg) if postinc else None


def _do_ldst_post(self, post):
    if post:
        expr, base, reg = post
        if expr:
            self.write_reg(reg, (base + (expr if expr > 0 else 0)) & MASK32, 0)
        else:
            self.write_reg(reg, base, 0)


_SIZES = {"ldb": 1, "ldbu": 1, "stb": 1, "ldh": 2, "ldhu": 2, "sth": 2,
          "ldb": 1, "ldw": 4, "stw": 4, "ldnw": 4, "stnw": 4, "lddw": 8,
          "stdw": 8, "ldndw": 8, "stndw": 8}


def EXEC(self, i):
    m = i.mnem
    ops = i.ops
    R = self.RI
    a = self.r
    c = self.cycle

    if m == "nop":
        return

    # --- data movement ---
    if m == "mvk":
        src, dst = ops[-2], ops[-1]
        _sext(_intop(src) & 0xFFFF, 16)
        self.write_reg(R(dst), _sext(_intop(src) & 0xFFFF, 16), 0)
        return
    if m == "mvkh":
        src, dst = ops[-2], ops[-1]
        v = _intop(src)
        if v is None:
            v = self.r[R(src)]
        else:
            v &= MASK32
        # objdump constant = the FULL 32-bit value; bits 31-16 replace
        hi = v & 0xFFFF0000
        base = self.r[R(dst)] & 0xFFFF
        self.write_reg(R(dst), hi | base, 0)
        return
    if m == "mv":
        src, dst = ops[-2], ops[-1]
        self.write_reg(R(dst), self.r[R(src)], 0)
        return
    if m == "addk":
        src, dst = ops[-2], ops[-1]
        self.write_reg(R(dst), (self.r[R(dst)] + _sext(_intop(src), 16)) & MASK32, 0)
        return
    if m == "add" or m == "sub":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = (v1 + v2 if m == "add" else v1 - v2) & MASK32
        self.write_reg(R(dst), r_, 0)
        return
    if m == "addkpc":
        cst, dst, nops = ops[-3], ops[-2], ops[-1]
        v = _intop(cst)
        if v is None:
            v = self.r[R(cst)]
        if abs(v) >= 0x8000:
            # objdump prints the absolute return address
            self.write_reg(R(dst), v & MASK32, 0)
        else:
            pce1 = i.addr & ~31
            self.write_reg(R(dst), (pce1 + _sext(v, 21)) & MASK32, 0)
        return
    if m == "and" or m == "or" or m == "xor" or m == "andn":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1 = _intop(s1) if _intop(s1) is not None else self.r[R(s1)]
        v2 = _intop(s2) if _intop(s2) is not None else self.r[R(s2)]
        r_ = (v1 & v2) if m == "and" else (v1 | v2) if m == "or" else \
             (v1 ^ v2) if m == "xor" else ((~v1) & v2)
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "ext" or m == "extu":
        # objdump prints EXT src2, cstA, dst or EXT src2, cstA, cstB, dst
        if len(ops) == 4:
            s2, cstA, cstB, dst = ops
        else:
            s2, cstA, dst = ops
            cstB = str(31 - int(_intop(cstA or '31'), 0) + 1)  # width
        v = self.r[R(s2)]
        sa = _intop(cstA) & 31
        sb = _intop(cstB) & 31
        width = sb - sa + 1
        if m == "ext":
            v = _sext(v, 32)
            r_ = (v >> sa) & ((1 << width) - 1) if sa + width < 32 \
                else _s32(v >> sa)
        else:
            r_ = (v >> sa) & ((1 << width) - 1) if sa + width < 32 \
                else (v >> sa) & MASK32
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "clr" or m == "set":
        s2, cstA, dst = ops[-3], ops[-2], ops[-1]
        # proper C6x: CLR src2, cstA, cstB, dst -- but objdump may print 3 ops
        cA = int(cstA, 0) & 31
        cB = int(ops[-2], 0) if len(ops) == 4 else 31
        v = self.r[R(s2)]
        if m == "clr":
            mask = ((1 << (cB - cA + 1)) - 1) << cA
            self.write_reg(R(dst), v & (~mask & MASK32), 0)
        else:
            mask = ((1 << (cB - cA + 1)) - 1) << cA
            self.write_reg(R(dst), v | mask, 0)
        return
    if m in ("cmpeq", "cmpgt", "cmplt", "cmpeq4", "cmpgtu", "cmpltu",
             "cmpgtu4", "cmpeq4", "cmpgt2", "cmplt2"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1 = _intop(s1)
        if v1 is None:
            v1 = self.r[R(s1)]
        else:
            v1 &= MASK32
        v2 = _intop(s2)
        if v2 is None:
            v2 = self.r[R(s2)]
        else:
            v2 &= MASK32
        if m == "cmpeq":
            r_ = 1 if v1 == v2 else 0
        elif m == "cmpgt":
            r_ = 1 if _s32(v1) > _s32(v2) else 0
        elif m == "cmplt":
            r_ = 1 if _s32(v1) < _s32(v2) else 0
        elif m == "cmpgtu":
            r_ = 1 if v1 > v2 else 0
        elif m == "cmpltu":
            r_ = 1 if v1 < v2 else 0
        elif m == "cmpeq4":
            r_ = 0
            if   (v1 & 0xFF) == (v2 & 0xFF): r_ |= 0x01
            if   (v1 >> 8 & 0xFF) == (v2 >> 8 & 0xFF): r_ |= 0x10 if False else 0x02
            if   (v1 >> 16 & 0xFF) == (v2 >> 16 & 0xFF): r_ |= 0x04
            if   (v1 >> 24 & 0xFF) == (v2 >> 24 & 0xFF): r_ |= 0x08
        elif m == "cmpgtu4":
            r_ = 0
            b1v, b2v = v1, v2
            for sh in (0, 8, 16, 24):
                r_ |= ((b1v >> sh & 0xFF) > (b2v >> sh & 0xFF)) << (sh // 8)
        elif m == "cmpgt2":
            r_ = 0
            if _s16(v1 & 0xFFFF) > _s16(v2 & 0xFFFF): r_ |= 1
            if _s16(v1 >> 16) > _s16(v2 >> 16): r_ |= 2
        elif m == "cmplt2":
            r_ = 0
            if _s16(v1 & 0xFFFF) < _s16(v2 & 0xFFFF): r_ |= 1
            if _s16(v1 >> 16) < _s16(v2 >> 16): r_ |= 2
        elif m == "cmplt2":
            r_ = 0
        self.write_reg(R(dst), r_, 0)
        return
    if m in ("ldw", "ldh", "ldhu", "ldb", "ldbu", "lddw", "ldnw", "ldndw"):
        sz = _SIZES[m]
        srcop, dst = ops[-2], ops[-1]
        addr, post = _addr_of(self, srcop)
        v = self.mem.read(addr, sz)
        if m in ("ldh", "ldh"):
            v = _sext(v, 16) & MASK32
        elif m in ("ldh",):
            pass
        if m == "ldb":
            v = _sext(v & 0xFF, 8) & MASK32
        if post:
            expr, base, reg = post
            self.write_reg(R(reg), (base + expr) & MASK32, 0)
        if m == "lddw":
            lo = v & MASK32
            hi = (v >> 32) & MASK32
            # dst like 'a13:a12' or 'a11:a10': text order is odd:even (hi:lo)
            if ":" in dst:
                hi_n, lo_n = dst.split(":")
            else:
                n = int(dst[1:])
                if n % 2:
                    hi_n, lo_n = dst[0] + str(n), dst[0] + str(n - 1)
                else:
                    lo_n, hi_n = dst[0] + str(n), dst[0] + str(n + 1)
            self.write_reg(R(lo_n), lo, 4)
            self.write_reg(R(hi_n), hi, 4)
            return
        self.write_reg(R(dst), v & MASK32, 4)
        return
    if m in ("stw", "stb", "sth", "stdw", "stnw", "stndw"):
        sz = _SIZES[m]
        src, dstop = ops[-2], ops[-1]
        addr, post = _addr_of(self, dstop)
        v = self.r[R(src)]
        if m == "stb":
            v &= 0xFF
        elif m == "sth":
            v &= 0xFFFF
        if post:
            expr, base, reg = post
            self.write_reg(R(reg), (base + expr) & MASK32, 0)
        self.write_mem(addr, sz, v)
        return
    if m in ("addaw", "addah", "addab", "subaw", "subah", "subab"):
        s2, s1, dst = ops[-3], ops[-2], ops[-1]
        sc = {"aw": 4, "ah": 2, "ab": 1}[m[-2:]]
        v2 = self.r[R(s2)] if not s2.lstrip("-").lstrip("+").isdigit() else int(s2, 0)
        v1 = self.r[R(s1)] if not s1.lstrip("-").lstrip("+").isdigit() else int(s1, 0)
        self.write_reg(R(dst), (v1 + (m[0] == "a" and 1 or -1) * abs(sc * v2)) &
                       MASK32, 0)
        return
    if m == "shl" or m == "shr" or m == "shru":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        sh = self.r[R(s1)] & 31
        v = self.r[R(s2)]
        if m == "shl":
            r_ = (v << sh) & MASK32
        elif m == "shr":
            r_ = (_s32(v) >> sh) & MASK32
        else:
            r_ = (v >> sh) & MASK32
        self.write_reg(R(dst), r_, 0)
        return
    if m == "addsp" or m == "subsp":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        x = struct.unpack("<f", struct.pack("<I", self.r[R(s1)]))[0]
        y = struct.unpack("<f", struct.pack("<I", self.r[R(s2)]))[0]
        r_ = _f32(x + y if m == "addsp" else x - y)
        self.write_reg(R(dst), struct.unpack("<I", struct.pack("<f", r_))[0], 3)
        return
    if m == "mpysp":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        x = struct.unpack("<f", struct.pack("<I", self.r[R(s1)]))[0]
        y = struct.unpack("<f", struct.pack("<I", self.r[R(s2)]))[0]
        r_ = _f32(x * y)
        self.write_reg(R(dst), struct.unpack("<I", struct.pack("<f", r_))[0], 3)
        return
    if m in ("mpy", "mpyh", "mpylh", "mpyhl", "mpyu", "mpyus", "mpysu",
              "mpyhsu", "mpyhu", "smpy", "smpyh", "smpylh", "smpyhl"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        f = {"mpy":  (lambda x, y: _s16(x) * _s16(y)),
             "mpyh": (lambda x, y: _s16(x >> 16) * _s16(y >> 16)),
             "mpylh": (lambda x, y: _s16(x) * _s16(y >> 16)),
             "mpyhl": (lambda x, y: _s16(x >> 16) * _s16(y)),
             "mpyu": (lambda x, y: (x & 0xFFFF) * (y & 0xFFFF)),
             "mpyus": (lambda x, y: (x & 0xFFFF) * _s16(y)),
             "mpysu": (lambda x, y: _s16(x) * (y & 0xFFFF)),
             "mpyhsu": (lambda x, y: _s16(x >> 16) * ((y >> 16) & 0xFFFF)),
             "mpyhu": (lambda x, y: (x >> 16 & 0xFFFF) * (y & 0xFFFF)),
             "smpy": (lambda x, y: (lambda p: (p << 1) & MASK32 if (p << 1) != 0x80000000 else 0x7FFFFFFF)(_s16(x) * _s16(y))),
             "smpyh": (lambda x, y: (lambda p: (p << 1) & MASK32 if (p << 1) != 0x80000000 else 0x7FFFFFFF)(_s16(x >> 16) * _s16(y >> 16))),
             "smpylh": (lambda x, y: (lambda p: (p << 1) & MASK32 if (p << 1) != 0x80000000 else 0x7FFFFFFF)(_s16(x) * _s16(y >> 16))),
             "smpyhl": (lambda x, y: (lambda p: (p << 1) & MASK32 if (p << 1) != 0x80000000 else 0x7FFFFFFF)(_s16(x >> 16) * _s16(y)))}[m]
        self.write_reg(R(dst), f(v1, v2) & MASK32, 1)
        return
    if m == "mvc":
        src, dst = ops[-2], ops[-1]
        # objdump prints control register names lowercase (fadcr, fmcr, ilc, amr...)
        src_l, dst_l = src.lower(), dst.lower()
        if src_l in IS_CTRL_LOW and dst_l not in IS_CTRL_LOW:
            self.write_reg(R(dst), self.ctl[src_l.upper()], 0)
            return
        if dst_l in IS_CTRL_LOW and src_l not in IS_CTRL_LOW:
            name = src_l.upper()
            if name == "ILC":
                self.iq.append((c + 3, self.r[R(src)] & MASK32))   # 4-cycle ILC lag
                return
            self.write_ctrl(name, self.r[R(src)], 0)
            return
        self.write_reg(R(dst), self.r[R(src)], 0)
        return
    if m in ("b", "bnop", "callp", "bdec", "bpos", "addkpc"):
        if m == "b":
            tgt = ops[-1]
            if tgt.startswith("0x") or (tgt.lstrip("-").isdigit()):
                self.branch(_intop(tgt) & MASK32, 5)
                return
            if tgt.lower() == "irp":
                self.branch(self.ctl["IRP"], 5)
            elif tgt.lower() == "nrp":
                self.branch(self.ctl["NRP"], 5)
            elif tgt.lower() == "b3":
                self.branch(self.r[reg_id("B3")], 5)
            else:
                self.branch(self.r[R(tgt)], 5)
            return
        if m == "bnop":
            tgt, nops = ops[-2], ops[-1]
            v = _intop(tgt)
            if v is None:
                self.branch(self.r[R(tgt)], 5)
            elif abs(v) >= 0x8000:
                # objdump prints the resolved absolute target
                self.branch(v & MASK32, 5)
            else:
                self.branch((i.addr + (_sext(v, 21) << 2)) & MASK32, 5)
            return
        if m == "bdec":
            tgt, src = ops[-2], ops[-1]
            v = self.r[R(src)] - 1 if False else self.r[R(src)] & 0xFFFFFFFC
            self.write_reg(R(src), v, 1)
            self.branch((i.addr + (_sext(int(tgt, 0), 21) << 2)) & MASK32, 5)
            return
        if m == "bpos":
            tgt, src = ops[-2], ops[-1]
            if _s32(self.r[R(src)]) >= 0:
                self.branch(i.addr + (_sext(int(tgt, 0), 21) << 2), 5)
            return
        if m == "callp":
            # CALLP = B + B3 <- PC+4 (return addr), 5 delay slots
            tgt = ops[-1]
            self.write_reg(reg_id("B3"), (i.addr + 4) & MASK32, 5)
            self.branch(self.r[R(tgt)], 5)
            return
    if m == "sploop" or m == "sploopd" or m == "sploopw":
        self._sploop_start(i, m)
        return
    if m == "spkernel":
        self._spkernel(i)
        return
    if m == "spmask" or m == "spmaskr":
        # masking semantics: we ignore masking (post-processing build);
        # recorded for trace only
        return
    if m == "mpy32":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        p = _s32(self.r[R(s1)]) * _s32(self.r[R(s2)])
        de, do = _pair(dst)
        self.write_reg(R(de), p & MASK32, 3)
        self.write_reg(R(do), (p >> 32) & MASK32, 3)
        return
    if m in ("mpy32u", "mpy32su", "mpy32us"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        x, y = self.r[R(s1)], self.r[R(s2)]
        if m == "mpy32u":
            p = x * y
        elif m == "mpy32su":
            p = _s32(x) * y
        else:
            p = x * _s32(y)
        de, do = _pair(dst)
        self.write_reg(R(de), p & MASK32, 3)
        self.write_reg(R(do), (p >> 32) & MASK32, 3)
        return
    if m == "mpyi":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        cst = _intop(s2)
        if cst is None:
            cst = None
        p = _s32(v1) * (_s32(v2) if cst is None else cst)
        self.write_reg(R(dst), p & MASK32, 8)
        return
    if m == "mpyid":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        p = _s32(self.r[R(s1)]) * _s32(self.r[R(s2)])
        de, do = _pair(dst)
        self.write_reg(R(de), p & MASK32, 9)
        self.write_reg(R(do), (p >> 32) & MASK32, 9)
        return
    if m in ("add2", "sub2", "addsub", "addsub2", "subaddsub"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        lo = _s16(v1) + _s16(v2) if m.startswith("add") else _s16(v1) - _s16(v2)
        hi = _s16(v1 >> 16) + _s16(v2 >> 16) if m.startswith("add") else \
            _s16(v1 >> 16) - _s16(v2 >> 16)
        r_ = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "sadd":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        r_ = _sat_s32(_s32(self.r[R(s1)]) + _s32(self.r[R(s2)]))
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "ssub":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        r_ = _sat_s32(_s32(self.r[R(s1)]) - _s32(self.r[R(s2)]))
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m in ("sadd2", "ssub2", "saddus2"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        lo = _sat_s16(_s16(v1) + _s16(v2)) if m == "sadd2" else \
            _sat_s16(_s16(v1) - _s16(v2)) if m == "ssub2" else \
            _u16_simple(v1, v2)
        hi = _sat_s16(_s16(v1 >> 16) + _s16(v2 >> 16)) if m == "sadd2" else \
            _sat_s16(_s16(v1 >> 16) - _s16(v2 >> 16)) if m == "ssub2" else \
            _u16_hi(v1, v2)
        r_ = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
        self.write_reg(R(dst), r_, 0)
        return
    if m == "add2adjiff":
        pass
    if m in ("add2",):   # duplicated earlier branch, keep last
        pass
    if m in ("addah",):
        pass
    if m == "avg2":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        lo = (_s16(v1) + _s16(v2) + 1) >> 1
        hi = (_s16(v1 >> 16) + _s16(v2 >> 16) + 1) >> 1
        r_ = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
        self.write_reg(R(dst), r_ & MASK32, 1)
        return
    if m == "avg2u" or m == "avgu4":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = 0
        for sh in (0, 8, 16, 24):
            r_ |= (((v1 >> sh & 0xFF) + (v2 >> sh & 0xFF) + 1) >> 1) << sh
        self.write_reg(R(dst), r_, 1)
        return
    if m == "pack2":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = ((v1 & 0xFFFF) << 16) | (v2 & 0xFFFF)
        self.write_reg(R(dst), r_, 0)
        return
    if m == "packh2":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = ((v1 & 0xFFFF0000)) | ((v2 >> 16) & 0xFFFF)
        self.write_reg(R(dst), r_, 0)
        return
    if m == "packlh2":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = ((v1 & 0xFFFF) << 16) | ((v2 >> 16) & 0xFFFF)
        self.write_reg(R(dst), r_, 0)
        return
    if m == "packhl2":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = (v1 & 0xFFFF0000) | (v2 & 0xFFFF)
        self.write_reg(R(dst), r_, 0)
        return
    if m == "packh4":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = (v1 & 0xFF000000) | ((v1 & 0x00FF0000) >> 8) | \
             (v2 & 0xFF000000) >> 16 | ((v2 & 0x00FF0000) >> 24)
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "packl4":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = ((v1 << 8) & 0xFF000000) | (v1 & 0x0000FF00) | \
             ((v2 << 8) & 0x0000FF00) | (v2 & 0xFF)
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "spack2":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        lo = 0x7FFF if _s32(v2) > 0x7FFF else 0x8000 if _s32(v2) < -32768 \
            else v2 & 0xFFFF
        hi = 0x7FFF if _s32(v1) > 0x7FFF else 0x8000 if _s32(v1) < -32768 \
            else (v1 >> 16) & 0xFFFF
        self.write_reg(R(dst), (hi << 16) | lo, 0)
        return
    if m in ("unpklu4", "unpkhu4"):
        s2, dst = ops[-2], ops[-1]
        v = self.r[R(s2)]
        if m == "unpklu4":
            r_ = (v & 0xFF) | (((v >> 8) & 0xFF) << 16)
        else:
            r_ = (((v >> 16) & 0xFF) << 24) | ((v >> 24) & 0xFF)
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m in ("min2", "max2", "minu4", "maxu4"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        pick_min = m.startswith("min")
        r_ = 0
        lanes = 2 if m.endswith("2") else 4
        for k in range(lanes):
            sh = 16 * k if lanes == 2 else 8 * k
            x, y = (v1 >> sh) & ((1 << (16 if lanes == 2 else 8)) - 1), \
                   (v2 >> sh) & ((1 << (16 if lanes == 2 else 8)) - 1)
            if lanes == 2:
                x, y = _s16(x << sh) >> sh if False else _s16(x), _s16(y)
            cmp_ = x <= y
            take = (x if cmp_ else y) if pick_min else (x if x >= y else y)
            r_ |= (take & ((1 << (16 if lanes == 2 else 8)) - 1)) << sh
        self.write_reg(R(dst), r_, 0)
        return
    if m == "dotp2":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        p = _s16(v1) * _s16(v2) + _s16(v1 >> 16) * _s16(v2 >> 16)
        self.write_reg(R(dst), p & MASK32, 1)
        return
    if m in ("dotpsu4", "dotpu4", "dotprsu2", "dotpnrsu2", "dotpn2"):
        if m in ("dotpu4", "dotpsu4"):
            s1, s2, dst = ops[-3], ops[-2], ops[-1]
            v1, v2 = self.r[R(s1)], self.r[R(s2)]
            r_ = 0
            for k in range(4):
                a_ = (v1 >> 8 * k) & 0xFF
                b_ = (v2 >> 8 * k) & 0xFF
                a_ = a_ if m == "dotpu4" else a_
                r_ += a_ * b_
            self.write_reg(R(dst), r_ & MASK32, 1)
            return
        if m == "dotprsu2":
            s1, s2, dst = ops[-3], ops[-2], ops[-1]
            v1, v2 = self.r[R(s1)], self.r[R(s2)]
            p = (v1 & 0xFFFF) * _s16(v2 & 0xFFFF) + \
                ((v1 >> 16) & 0xFFFF) * _s16(v2 >> 16)
            p = (p + 0x8000) & ~0xFFFF
            self.write_reg(R(dst), _sat_s32(p) & MASK32, 2)
            return
        if m == "dotpn2":
            s1, s2, dst = ops[-3], ops[-2], ops[-1]
            v1, v2 = self.r[R(s1)], self.r[R(s2)]
            p = _s16(v1) * -(_s16(v2)) + _s16(v1 >> 16) * _s16(v2 >> 16)
            self.write_reg(R(dst), p & MASK32, 2)
            return
    if m in ("dpop3",):
        pass
    if m in ("cmpy", "cmpyr", "cmpyr1", "cmpy", "cmpy_c"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        de, do = _pair(dst)
        img = _s16(v1) * _s16(v2 >> 16) + _s16(v1 >> 16) * _s16(v2)
        re_ = _s16(v1 >> 16) * _s16(v2 >> 16) - _s16(v1) * _s16(v2)
        self.write_reg(R(de), img & MASK32, 3)
        self.write_reg(R(do), re_ & MASK32, 3)
        return
    if m == "dmv":
        s2, s1, dst = ops[-3], ops[-2], ops[-1]
        de, do = _pair(dst)
        self.write_reg(R(de), self.r[R(s2)], 0)
        self.write_reg(R(do), self.r[R(s1)], 0)
        return
    if m in ("ddotp4", "ddotpl2r"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        de, do = _pair(dst)
        pe = (v1 >> 16 & 0xFF) * (v2 >> 16 & 0xFF) + (v1 & 0xFF) * (v2 & 0xFF)
        po = (v1 >> 16 & 0xFF) * (v2 >> 24 & 0xFF) + (v1 & 0xFF) * (v2 >> 8 & 0xFF)
        self.write_reg(R(de), pe, 3)
        self.write_reg(R(do), po, 3)
        return
    if m == "lopd" or m == "lopd":
        pass
    if m in ("norm",):
        s2, dst = ops[-2], ops[-1]
        v = self.r[R(s2)]
        r_ = 0 if v == 0 else (31 - (v.bit_length() - 1)) if not (v & 0x80000000) \
            else 31 - ((~v & MASK32).bit_length() - 1)
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "lmbd":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        bit = self.r[R(s1)] & 1
        v = self.r[R(s2)]
        if bit:
            n = 32
            for k in range(32):
                if v >> (31 - k) & 1:
                    n = k; break
        else:
            n = 32
            for k in range(32):
                if not (v >> (31 - k) & 1):
                    n = k; break
        self.write_reg(R(dst), n, 0)
        return
    if m == "rotl":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        n = self.r[R(s1)] & 31
        v = self.r[R(s2)]
        r_ = ((v << n) | (v >> (32 - n))) if n else v
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m in ("sshl", "sshl_r"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        n = self.r[R(s1)] & 31
        v = _s32(self.r[R(s2)])
        shifted = (v << n) & MASK32
        # saturation check: sign bits
        sv = (v >> (31 - n)) if n else (v >> 31)
        svr = (-1) if v < 0 else 0
        if n and (sv & MASK32) != (svr & MASK32):
            sat = 0x7FFFFFFF if v > 0 else 0x80000000
            self.write_reg(R(dst), sat & MASK32, 0)
            self.ctl["SSR"] |= 1
        else:
            self.write_reg(R(dst), shifted, 0)
        return
    if m in ("sshvl", "sshvr"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        n = _sext(self.r[R(s1)] & 0xFFFF, 16)
        v = _s32(self.r[R(s2)])
        if n >= 0:
            r_ = _sat_s32(v << n)
        else:
            r_ = v >> min(-n, 31)
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "subc":
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        x, y = self.r[R(s1)], self.r[R(s2)]
        if x - y >= 0:
            r_ = ((x - y) << 1) | 1
        else:
            r_ = x << 1
        self.write_reg(R(dst), r_ & MASK32, 0)
        return
    if m == "sel":
        pass
    if m in ("cmtl",):
        pass
    if m in ("saddsub", "saddsub2", "saddu4", "saddus2"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        if m in ("saddsub",):
            de, do = _pair(dst)
            self.write_reg(R(de), _sat_s32(_s32(v1) + _s32(v2)) & MASK32, 0)
            self.write_reg(R(do), _sat_s32(_s32(v1) - _s32(v2)) & MASK32, 0)
            return
        if m == "saddsub2":
            lo = _sat_s16(_s16(v1) + _s16(v2))
            hi = _sat_s16(_s16(v1) - _s16(v2))
            self.write_reg(R(dst), ((hi & 0xFFFF) << 16) | (lo & 0xFFFF), 0)
            return
        if m == "saddu4":
            r_ = 0
            for k in range(4):
                r_ |= min((v1 >> 8 * k & 0xFF) + (v2 >> 8 * k & 0xFF), 0xFF) << 8 * k
            self.write_reg(R(dst), r_, 0)
            return
        if m == "saddus2":
            lo = min(max((v1 & 0xFFFF) + _s16(v2), 0), 65535)
            hi = min(max((v1 >> 16) + _s16(v2 >> 16), 0), 65535)
            self.write_reg(R(dst), (hi << 16) | lo, 0)
            return
    if m in ("subabs4",):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = 0
        for k in range(4):
            r_ |= abs((v1 >> 8 * k & 0xFF) - (v2 >> 8 * k & 0xFF)) << 8 * k
        self.write_reg(R(dst), r_, 0)
        return
    if m in ("add4", "sub4"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = 0
        for k in range(4):
            d = ((v1 >> 8 * k & 0xFF) - (v2 >> 8 * k & 0xFF)) \
                if m == "sub4" else ((v1 >> 8 * k & 0xFF) + (v2 >> 8 * k & 0xFF))
            r_ |= (d & 0xFF) << 8 * k
        self.write_reg(R(dst), r_, 0)
        return
    if m in ("gmpy4",):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        self.stop("gmpy4", i.addr)
        return
    if m in ("spdp", "dpint", "dpsp", "dptrunc", "dptrunk"):
        s2, dst = ops[-2], ops[-1]
        v = self.r[R(s2)]
        if m == "spdp":
            x = struct.unpack("<f", struct.pack("<I", v))[0]
            lo, hi = _split_dp(x)
            de, do = _pair(dst)
            self.write_reg(R(de), lo, 1)
            self.write_reg(R(do), hi, 2)
            return
        else:
            de, do = _pair(s2)
            x = _dp(self.r[R(de)], self.r[R(do)])
            if m == "dpint":
                r_ = int(x) & MASK32
                self.write_reg(R(dst), r_, 3)
            elif m == "dpsp":
                r_ = struct.unpack("<I", struct.pack("<f", _f32(x)))[0]
                self.write_reg(R(dst), r_, 3)
            else:
                r_ = math.trunc(x) & MASK32
                self.write_reg(R(dst), r_, 3)
            return
    if m in ("spint", "sptrunc"):
        s2, dst = ops[-2], ops[-1]
        x = struct.unpack("<f", struct.pack("<I", self.r[R(s2)]))[0]
        r_ = int(x) if m == "spint" else math.trunc(x)
        self.write_reg(R(dst), r_ & MASK32, 3)
        return
    if m in ("intsp", "intspu", "intdp", "intdpu"):
        s2, dst = ops[-2], ops[-1]
        v = self.r[R(s2)]
        x = _s32(v) if m in ("intsp", "intdp") else v
        if m.startswith("intsp"):
            r_ = struct.unpack("<I", struct.pack("<f", _f32(float(x))))[0]
            self.write_reg(R(dst), r_, 3)
        else:
            lo, hi = _split_dp(float(x))
            de, do = _pair(dst)
            self.write_reg(R(de), lo, 4)
            self.write_reg(R(do), hi, 5)
        return
    if m in ("abs", "abs2", "abssp", "absdp"):
        if m in ("abs", "abs2"):
            s2, dst = ops[-2], ops[-1]
            if m == "abs":
                r_ = abs(_s32(self.r[R(s2)])) & MASK32
            else:
                v = self.r[R(s2)]
                lo = abs(_s16(v)) & 0xFFFF
                hi = abs(_s16(v >> 16)) & 0xFFFF
                r_ = (hi << 16) | lo
            self.write_reg(R(dst), r_, 0)
            return
        if m == "abssp":
            s2, dst = ops[-2], ops[-1]
            x = struct.unpack("<f", struct.pack("<I", self.r[R(s2)]))[0]
            r_ = struct.unpack("<I", struct.pack("<f", abs(x)))[0]
            self.write_reg(R(dst), r_, 3)
            return
        if m == "absdp":
            s2, dst = ops[-2], ops[-1]
            de, do = _pair(s2)
            x = abs(_dp(self.r[R(de)], self.r[R(do)]))
            lo, hi = _split_dp(x)
            de, do = _pair(dst)
            self.write_reg(R(de), lo, 1)
            self.write_reg(R(do), hi, 2)
            return
    if m in ("sub", "add"):
        pass
    if m in ("addu", "subu"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        v1, v2 = self.r[R(s1)], self.r[R(s2)]
        r_ = (v1 + v2) if m == "addu" else (v1 - v2)
        de, do = _pair(dst)
        self.write_reg(R(de), r_ & MASK32, 0)
        self.write_reg(R(do), (r_ >> 32) & MASK32, 0)
        return
    if m in ("sadd/spack2",):
        pass
    if m in ("cmpeqsp", "cmpgtsp", "cmpltsp", "cmpeqdp", "cmpgtdp", "cmpltdp"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        if m.endswith("sp"):
            x = struct.unpack("<f", struct.pack("<I", self.r[R(s1)]))[0]
            y = struct.unpack("<f", struct.pack("<I", self.r[R(s2)]))[0]
        else:
            de, do = _pair(s1)
            x = _dp(self.r[R(de)], self.r[R(do)])
            de, do = _pair(s2)
            y = _dp(self.r[R(de)], self.r[R(do)])
        if "eq" in m:
            r_ = x == y
        elif "lt" in m:
            r_ = x < y
        else:
            r_ = x > y
        self.write_reg(R(dst), int(r_), 2 if m.endswith("dp") else 1)
        return
    if m in ("adddp", "subdp", "mpydp"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        de, do = _pair(s1)
        x = _dp(self.r[R(de)], self.r[R(do)])
        de, do = _pair(s2)
        y = _dp(self.r[R(de)], self.r[R(do)])
        z = x + y if m == "adddp" else x - y if m == "subdp" else x * y
        lo, hi = _split_dp(z)
        de, do = _pair(dst)
        self.write_reg(R(de), lo, 7 if m == "mpydp" else 6)
        self.write_reg(R(do), hi, 8 if m == "mpydp" else 7)
        return
    if m in ("mpyspdp", "mpysp2dp"):
        s1, s2, dst = ops[-3], ops[-2], ops[-1]
        x = struct.unpack("<f", struct.pack("<I", self.r[R(s1)]))[0]
        y = _dp(*[self.r[R(rr)] for rr in _split_pair_ops(s2)])
        z = x * y
        lo, hi = _split_dp(z)
        de, do = _pair(dst)
        self.write_reg(R(de), lo, 4)
        self.write_reg(R(do), hi, 5)
        return
    if m in ("rsqrsp", "rsqrdp"):
        s2, dst = ops[-2], ops[-1]
        if m == "rsqrsp":
            x = struct.unpack("<f", struct.pack("<I", self.r[R(s2)]))[0]
            r_ = 1.0 / math.sqrt(x) if abs(x) > 1e-300 else float("inf")
            self.write_reg(R(dst), struct.unpack("<I", struct.pack("<f", _f32(r_)))[0], 3)
        else:
            de, do = _pair(s2)
            x = _dp(self.r[R(de)], self.r[R(do)])
            r_ = 1.0 / math.sqrt(abs(x)) if abs(x) > 1e-300 else float("inf")
            lo, hi = _split_dp(r_)
            de, do = _pair(dst)
            self.write_reg(R(de), lo, 3)
            self.write_reg(R(do), hi, 4)
        return
    if m in ("mpyhsu",):
        pass
    # --- bit-extension instructions ignored in the first build
    if m in ("xpnd2", "xpnd4", "cmpy", "mpylshu", "mpylhu", "mpyhuls",
              "mpyhslu", "mpyhi", "mpyhir", "mpyli", "mpylir", "smpy32",
              "shr2", "shlmb", "shrmb", "pack2", "addad", "smpy2", "minu4",
              "cmpyr", "cmpyr1", "ddotpl2r", "saddsub2"):
        self.stop(f"unimpl-todo:{m}", i.addr)
        return
    if m in ("subsp/branch",):
        pass
    # --- thinks like pack2/select/etc
    if m.startswith("spmask"):
        return
    self.stop(f"unimpl:{m}", i.addr)


def _sat_s16(x):
    return 0x7FFF if x > 32767 else (0x8000 if x < -32768 else x & 0xFFFF)


def _u16_simple(v1, v2):
    return min((v1 & 0xFFFF) + _s16(v2 & 0xFFFF), 0xFFFF)


def _u16_hi(v1, v2):
    return min((v1 >> 16) + _s16((v2 >> 16) & 0xFFFF), 0xFFFF)


def _pair(dst):
    """dst reg name -> (even, odd) pair with odd high half."""
    n = int(dst[1:])
    return (dst[0] + str(n if n % 2 == 0 else n - 1),
            dst[0] + str(n if n % 2 == 1 else n + 1))


def _split_pair_ops(s):
    return s


Core.EXEC = EXEC
Core.EXEC.__name__ = "EXEC"


# -----------------------------------------------------------------------
# SPLOOP engine (SPRUFE8B §7). Model: as SPLOOP body packets execute the
# FIRST time, they are captured into sp.body (a cycle list, one packet per
# cycle index). After the last-load stage boundary (SPKERNEL seen), modes:
#   kernel: replay body[cycle % body_len]
#   drain:  replay body[cycle % body_len] if cycle_in_stage >= remaining
# Termination (§7.10): SPLOOPW condition reg snapshot 3 cycles old. With no
# epilog (SPLOOPW), abort at the first stage boundary after the condition
# turns false (3-cycle piped delay). SPMASK: skip those packets entirely.
# -----------------------------------------------------------------------

def _sploop_start(self, i, m):
    """Record the loop buffer start. The FIRST loop through, the packets
    that follow are executed AND captured; replay begins after SPKERNEL."""
    sp = self.sp
    sp.active = True
    sp.mode = 0            # loading
    sp.kind = m
    sp.ii = _intop(i.ops[-1]) if i.ops and _intop(i.ops[-1]) is not None else 1
    sp.body = []
    sp.body_cyc = []
    if m == "sploopw" and i.cond:
        sp.wcond = (reg_id(i.cond[0]), i.cond[1])
    else:
        sp.wcond = None
    sp.pc_after = i.addr + 4
    self.log(f"SPLOOP start kind={m} ii={sp.ii}")


Core._sploop_start = _sploop_start


def _spkernel(self, i):
    sp = self.sp
    if not sp.active:
        return
    # objdump text: spkernel fstg,fcyc or no args
    if len(i.ops) >= 2:
        sp.spk_f = _intop(i.ops[-2]) or 0
        sp.spk_c = _intop(i.ops[-1]) or 0
    else:
        sp.spk_f = 0
        sp.spk_c = 0
    sp.body_len = max(1, len(sp.body_cyc))
    sp.mode = 1
    sp.draining = 0
    sp.kframe = 0
    self.log(f"SPKERNEL body={len(sp.body_cyc)} cycles")


Core._spkernel = _spkernel


def _sp_cycle(self):
    """Run one loop-buffer cycle."""
    sp = self.sp
    g = self.cycle - sp.start_cyc
    if sp.mode == 0:    # loading (first pass): fall through to normal fetch;
                        # the packets capture themselves via hooks
        pc = self.pc
        self._exec_packet(pc)
        self._capture(pc)
        return
    if sp.mode == 1:    # kernel: replay
        k = g - sp.k0
        if k < 0:
            return
        idx = k % sp.body_len
        self._replay(idx)
    else:               # drain
        for idx in range(sp.drain_idx, sp.body_len):
            self._replay(idx)
        sp.active = False
        self.pc = sp.pc_after

Core._sp_cycle = _sp_cycle


def _capture(self, pc):
    """Hooked into _exec_packet while loading."""
    pass
