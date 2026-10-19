"""SH-4 FPU. Encodings from R01UH0174EJ Tables 3.10-3.12.

Size/bank model (the manual's single/double rows share bit patterns; mode
bits select the view -- the only self-consistent reading, and it matches
gcc codegen):
  SZ=0: single FR moves/ops. FRn bank = FR bit. Bit 8/4 are register bits.
  SZ=1: double moves/ops. Dest pair = bits 11:9, dest bank = bit 8;
        src pair = bits 7:5, src bank = bit 4. (This is exactly the manual's
        DRm/XDm distinction: DR = bank 0, XD = bank 1.)
Single-reg group (FSTS..FCNVDS, no XD rows): SZ merges to DR pairs in the
current FR bank. FIPR/FTRV always bank 0 vectors. FSCA target follows DR
bank bits like other double moves.

Values are Python floats. FD=1 raises the FPU exception. NaN compares
unordered (Python gives False). Single ops don't re-round.
"""
import math
import struct

from .sh4 import SHException


def _f2u(x):
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


def _u2f(u):
    return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]


def _d2u(x):
    return struct.unpack("<Q", struct.pack("<d", float(x)))[0]


def _u2d(u):
    return struct.unpack("<d", struct.pack("<Q", u & 0xFFFFFFFFFFFFFFFF))[0]


def _div(a, b):
    try:
        return a / b
    except ZeroDivisionError:        # IEEE: x/0 = signed inf, 0/0 = NaN
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def _mul(a, b):
    try:
        return a * b
    except OverflowError:
        s = -1.0 if (a < 0) != (b < 0) else 1.0
        return s * math.inf


class FPU:
    def __init__(self, cpu):
        self.c = cpu

    # -- state ----------------------------------------------------------
    def bank(self):
        return 1 if (self.c.fpscr >> 21) & 1 else 0

    def sz(self):
        return (self.c.fpscr >> 20) & 1

    def getf(self, i):
        return self.c.fr[self.bank()][i & 15]

    def setf(self, i, v):
        # FR registers are 32-bit: round like the hardware
        try:
            self.c.fr[self.bank()][i & 15] = _u2f(_f2u(v))
        except OverflowError:
            self.c.fr[self.bank()][i & 15] = math.copysign(math.inf, v)

    def getraw(self, i):
        return _f2u(self.c.fr[self.bank()][i & 15])

    def setraw(self, i, u):
        self.c.fr[self.bank()][i & 15] = _u2f(u)

    def _getpair(self, pair, bank):
        b = self.c.fr[bank & 1]
        i = (pair & 7) * 2
        return _f2u(b[i]), _f2u(b[i + 1])

    def _setpair(self, pair, bank, hl):
        b = self.c.fr[bank & 1]
        i = (pair & 7) * 2
        b[i], b[i + 1] = _u2f(hl[0]), _u2f(hl[1])

    def getd(self, pair, bank):
        b = self.c.fr[bank & 1]
        i = (pair & 7) * 2
        return _u2d((_f2u(b[i]) << 32) | _f2u(b[i + 1]))

    def setd(self, pair, bank, v):
        b = self.c.fr[bank & 1]
        i = (pair & 7) * 2
        u = _d2u(v)
        b[i] = _u2f((u >> 32) & 0xFFFFFFFF)
        b[i + 1] = _u2f(u & 0xFFFFFFFF)

    def getd8(self, addr):
        raw = bytes(self.c.mem.r8(self.c.translate(addr + k) & 0xFFFFFFFF)
                    for k in range(8))
        return struct.unpack("<d", bytes(raw))[0]

    def setd8(self, addr, v):
        raw = struct.pack("<d", float(v))
        for k in range(8):
            self.c.mem.w8(self.c.translate(addr + k) & 0xFFFFFFFF, raw[k])

    # -- 32-bit FMOV (SH-4A): 0011nnnnmmmm0001 + disp halfword -------------
    def fmov32(self, w1, w2):
        c = self.c
        r = c.gr
        dbl = not (w1 & 0x0100)          # bit24 clear -> DR, disp x8
        n = (w1 >> 8) & (0x7 if dbl else 0xF)
        m = (w1 >> 4) & 0xF
        if (w2 & 0xF000) == 0x7000:      # load @(disp,Rm)
            disp = (w2 & 0xFFF) * (8 if dbl else 4)
            a = (r(m) + disp) & 0xFFFFFFFF
            if dbl:
                self.setd(n, 0, self.getd8(a))
            else:
                self.setf(n, _u2f(c._rd(a, 4)))
        elif (w2 & 0xF000) == 0x3000:    # store -> @(disp,Rn)
            disp = (w2 & 0xFFF) * (8 if dbl else 4)
            a = (r(n) + disp) & 0xFFFFFFFF
            if dbl:
                self.setd8(a, self.getd(n, 0))
            else:
                c._wr(a, 4, _f2u(self.getf(m)))
        else:
            raise SHException(0x100, 0x180)
        return ("adv4", False, 2)

    # -- 16-bit ops --------------------------------------------------------
    # FPSCR.PR (bit 19) selects arithmetic precision, FPSCR.SZ (bit 20) the
    # FMOV transfer size (manual Sec 6.3). An earlier version keyed double
    # arithmetic off SZ; the firmware computes with PR=1 SZ=0 (measured:
    # FADD/FMUL/FLOAT/FTRC all run in that mode), so every double-precision
    # calculation was done as singles on the wrong registers.
    def pr(self):
        return (self.c.fpscr >> 19) & 1

    def step(self, op, nxt):
        c = self.c
        if c.sr & (1 << 15):
            raise SHException(0x100, 0x120)
        n = (op >> 8) & 0xF
        m = (op >> 4) & 0xF
        r = c.gr
        T = c.set_t
        lo4 = op & 0xF
        sz = self.sz()
        pr = self.pr()
        bk = self.bank()
        ill = SHException(0x100, 0x180)

        # arithmetic / compare (0-5), FMAC (E)
        if lo4 in (0x0, 0x1, 0x2, 0x3, 0x4, 0x5):
            if pr:
                if op & 0x0110:
                    raise ill
                dp, sp = (op >> 9) & 0x7, (op >> 5) & 0x7
                a, b = self.getd(sp, bk), self.getd(dp, bk)
                if lo4 == 0x4:
                    T(1 if b == a else 0)
                    return None
                if lo4 == 0x5:
                    T(1 if b > a else 0)
                    return None
                res = (b + a, b - a, _mul(b, a), _div(b, a))[lo4]
                self.setd(dp, bk, res)
                return (None, False, (4, 4, 5, 18)[lo4])
            a, b = self.getf(m), self.getf(n)
            if lo4 == 0x4:
                T(1 if b == a else 0)
                return None
            if lo4 == 0x5:
                T(1 if b > a else 0)
                return None
            res = (b + a, b - a, _mul(b, a), _div(b, a))[lo4]
            self.setf(n, res)
            return (None, False, (3, 3, 3, 12)[lo4])
        if lo4 == 0xE:          # FMAC FR0,FRm,FRn (single only)
            if pr:
                raise ill
            self.setf(n, self.getf(0) * self.getf(m) + self.getf(n))
            return (None, False, 3)

        # FMOV reg-reg (C) and memory forms (6-B): SZ selects 64-bit pairs,
        # bit 8 / bit 4 pick the XD (other) bank for the pair
        if lo4 == 0xC:
            if sz:
                dp, db = (op >> 9) & 0x7, (op >> 8) & 0x1
                sp, sb = (op >> 5) & 0x7, (op >> 4) & 0x1
                self._setpair(dp, bk ^ db, self._getpair(sp, bk ^ sb))
            else:
                self.setraw(n, self.getraw(m))
            return None
        if lo4 in (0x8, 0x9, 0xA, 0xB, 0x6, 0x7):
            if sz:
                if lo4 in (0x8, 0x9, 0x6):      # loads: dest pair/bank
                    dp, db = (op >> 9) & 0x7, (op >> 8) & 0x1
                    a = r(m) if lo4 != 0x6 else (r(0) + r(m)) & 0xFFFFFFFF
                    lo, hi = c._rd(a, 4), c._rd((a + 4) & 0xFFFFFFFF, 4)
                    self._setpair(dp, bk ^ db, (hi, lo))
                    if lo4 == 0x9:
                        c.set_gr(m, (r(m) + 8) & 0xFFFFFFFF)
                else:                           # stores: src pair/bank
                    sp, sb = (op >> 5) & 0x7, (op >> 4) & 0x1
                    hi, lo = self._getpair(sp, bk ^ sb)
                    if lo4 == 0xB:
                        c.set_gr(n, (r(n) - 8) & 0xFFFFFFFF)
                    a = r(n) if lo4 != 0x7 else (r(0) + r(n)) & 0xFFFFFFFF
                    c._wr(a, 4, lo)
                    c._wr((a + 4) & 0xFFFFFFFF, 4, hi)
                return None
            if lo4 == 0x8:
                self.setraw(n, c._rd(r(m), 4))
            elif lo4 == 0x9:
                self.setraw(n, c._rd(r(m), 4))
                c.set_gr(m, (r(m) + 4) & 0xFFFFFFFF)
            elif lo4 == 0xA:
                c._wr(r(n), 4, self.getraw(m))
            elif lo4 == 0xB:
                c.set_gr(n, (r(n) - 4) & 0xFFFFFFFF)
                c._wr(r(n), 4, self.getraw(m))
            elif lo4 == 0x6:
                self.setraw(n, c._rd((r(0) + r(m)) & 0xFFFFFFFF, 4))
            else:
                c._wr((r(0) + r(n)) & 0xFFFFFFFF, 4, self.getraw(m))
            return None

        # FSCHG / FRCHG / FPCHG (exact opcodes)
        if op == 0xF3FD:
            c.fpscr ^= (1 << 20)
            return None
        if op == 0xFBFD:
            c.fpscr ^= (1 << 21)
            return None
        if op == 0xF7FD:
            c.fpscr ^= (1 << 19)
            return None

        # single-reg group 1111nnnnssss1101
        if lo4 == 0xD:
            sub = (op >> 4) & 0xF
            dn = (n >> 1) & 7                   # DRn pair when PR=1
            if sub == 0x0:      # FSTS FPUL,FRn
                self.setraw(n, c.fpul)
                return None
            if sub == 0x1:      # FLDS FRm,FPUL
                c.fpul = self.getraw(n)
                return None
            if sub == 0x2:      # FLOAT FPUL,FRn/DRn
                v = c.fpul - 0x100000000 if c.fpul & 0x80000000 else c.fpul
                if pr:
                    self.setd(dn, bk, float(v))
                else:
                    self.setf(n, float(v))
                return (None, False, 2)
            if sub == 0x3:      # FTRC FRm/DRm,FPUL
                v = self.getd(dn, bk) if pr else self.getf(n)
                if math.isnan(v):
                    c.fpul = 0x80000000
                elif v >= 2147483647.0:
                    c.fpul = 0x7FFFFFFF
                elif v <= -2147483648.0:
                    c.fpul = 0x80000000
                else:
                    c.fpul = int(v) & 0xFFFFFFFF
                return (None, False, 2)
            if sub in (0x4, 0x5):               # FNEG / FABS: sign bit only
                i = dn * 2 if pr else n
                u = self.getraw(i)
                self.setraw(i, (u ^ 0x80000000) if sub == 0x4 else (u & 0x7FFFFFFF))
                return None
            if sub == 0x6:      # FSQRT
                if pr:
                    vv = self.getd(dn, bk)
                    self.setd(dn, bk, math.sqrt(vv) if vv >= 0 else float("nan"))
                else:
                    v = self.getf(n)
                    self.setf(n, math.sqrt(v) if v >= 0 else float("nan"))
                return (None, False, 8)
            if sub == 0x7:      # FSRRA (single only)
                v = self.getf(n)
                self.setf(n, 1.0 / math.sqrt(v) if v > 0 else
                          (math.inf if v == 0 else float("nan")))
                return (None, False, 6)
            if sub == 0x8:      # FLDI0
                self.setraw(n, 0)
                return None
            if sub == 0x9:      # FLDI1
                self.setraw(n, 0x3F800000)
                return None
            if sub == 0xA:      # FCNVSD FPUL,DRn
                self.setd(dn, bk, _u2f(c.fpul))
                return (None, False, 3)
            if sub == 0xB:      # FCNVDS DRm,FPUL
                c.fpul = _f2u(self.getd(dn, bk))
                return (None, False, 3)
            if sub == 0xE:      # FIPR FVm,FVn (1111nnmm11101101), current bank
                vn = (op >> 10) & 0x3
                vm = (op >> 8) & 0x3
                s = sum(self.getf(vn * 4 + k) * self.getf(vm * 4 + k) for k in range(4))
                self.setf(vn * 4 + 3, s)
                return (None, False, 6)
            if sub == 0xF:
                if (op & 0x0300) == 0x0100:      # FTRV XMTRX,FVn
                    vn = (op >> 10) & 0x3
                    xb = c.fr[bk ^ 1]            # XMTRX = the other bank
                    vec = [self.getf(vn * 4 + k) for k in range(4)]
                    out = [sum(xb[k * 4 + i] * vec[k] for k in range(4))
                           for i in range(4)]
                    for i in range(4):
                        self.setf(vn * 4 + i, out[i])
                    return (None, False, 8)
                if not (op & 0x0100):            # FSCA FPUL,DRn
                    ang = (c.fpul & 0xFFFF) * (2 * math.pi / 65536.0)
                    dp = (op >> 9) & 0x7
                    self.setf(dp * 2, math.sin(ang))
                    self.setf(dp * 2 + 1, math.cos(ang))
                    return (None, False, 8)
            raise ill

        raise ill
