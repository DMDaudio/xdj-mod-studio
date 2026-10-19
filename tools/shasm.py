#!/usr/bin/env python3
"""shasm.py -- minimal SH-4 assembler (subset for firmware patchlets).

Supports: nop, rts, mov #imm,Rn, mov Rm,Rn, mov.b/w/l @Rm,Rn / @Rm+,Rn /
@(disp,Rm),Rn / R Armstrong,@-Rn / @(disp,GBR),R0 / Rm,@(disp,GBR) /
Rm,@Rn / Rm,@-Rn / Rm,@(disp,Rn), add/sub/add#/and/or/xor Rm,Rn,
add #imm,Rn, cmp/eq #imm,R0 / Rm,Rn, cmp/hs, cmp/hi, cmp/gt, cmp/ge,
tst Rm,Rn / tst #imm,R0 / tst.b #imm,@(disp,GBR), shll/shlr/shar/shll2/
shll8/shll16/shlr2/shlr8/shlr16 Rn, dt Rn, extu.b/w Rn,Rn, exts.*,
bra/bsr/bf/bt (disp auto), braf/bsrf, jmp/jsr @Rn, mov.l @(disp,PC),Rn
(pool auto), mova @(disp,PC),R0, ldc/stc Rm,sr/gbr, sleep, trapa #imm,
mova, nop. Labels + `.pool` auto-placement. Two-pass.

Usage: shasm.assemble(src) -> bytes. CLI: shasm.py file.asm -o out.bin
"""
import re, struct, sys

REG = {f"r{i}": i for i in range(16)}
REG.update({"r15": 15})


def R(s):
    s = s.strip().lower()
    if s not in REG:
        raise ValueError(f"bad reg {s!r}")
    return REG[s]


def parse_imm(s, lo, hi):
    v = int(s.strip(), 0)
    if not (lo <= v <= hi):
        raise ValueError(f"imm {v} out of [{lo},{hi}]")
    return v & 0xFFFFFFFF


class Asm:
    def __init__(self):
        self.code = []   # (kind, payload)
        self.labels = {}
        self.pools = []  # (index_in_code, value)

    def emit(self, w):
        self.code.append(("w", w & 0xFFFF))

    def emit_pool(self, value):
        self.code.append(("pool", value))

    def assemble(self, src):
        lines = []
        for ln in src.splitlines():
            ln = ln.split(";")[0].strip()
            if not ln:
                continue
            lines.append(ln)
        # pass 1: collect labels, expand
        addr = 0
        npool = 0                 # pool loads since the last .pool
        for ln in lines:
            if ln == ".pool":     # literal pool HERE (caller branches around)
                addr = (addr + 3) & ~3
                addr += 4 * npool
                npool = 0
                self.code.append(("flush", None))
                continue
            if re.match(r"mov\.l\s+@\(.+,\s*pc\)", ln, re.I):
                npool += 1
            if ln.endswith(":"):
                self.labels[ln[:-1].strip()] = addr
                continue
            m = re.match(r"\.long\s+(.+)", ln)
            if m:
                self.code.append(("long", int(m.group(1).strip(), 0)))
                addr += 4
                continue
            self.code.append(("ins", ln))
            addr += 2
        # pass 2: encode
        out = bytearray()
        pool_fix = []
        pc = 0
        for kind, pay in self.code:
            if kind == "w":
                out += struct.pack("<H", pay)
                pc += 2
            elif kind == "long":
                out += struct.pack("<I", pay)
                pc += 4
            elif kind == "flush":
                while len(out) % 4:
                    out += b"\x00"
                self._place(out, pool_fix)
                pc = len(out)
            elif kind == "pool":
                pool_fix.append((len(out), pay))
                out += b"\x00\x00\x00\x00"
                pc += 4
            else:
                w = self.enc(pay, pc, out, pool_fix)
                if w is not None:
                    out += struct.pack("<H", w)
                pc = len(out)     # pool-load placeholders advance pc too
        self._place(out, pool_fix)
        return bytes(out)

    def _place(self, out, pool_fix):
        """Append pending pc-relative pool entries at the end of out."""
        for entry in pool_fix:
            if len(entry) == 2:
                off, val = entry
                struct.pack_into("<I", out, off, val & 0xFFFFFFFF)
            else:
                off, ipc, lab, n = entry
                while len(out) % 4:
                    out += b"\x00"
                pa = len(out)
                disp = (pa - ((ipc + 4) & ~3)) // 4
                assert 0 <= disp < 256, f"pool too far ({lab})"
                struct.pack_into("<H", out, off, 0xD000 | (n << 8) | disp)
                try:
                    val = int(lab, 0)
                except ValueError:
                    val = self.labels[lab]
                out += struct.pack("<I", val & 0xFFFFFFFF)
        pool_fix.clear()

    # -- encoder ---------------------------------------------------------
    def enc(self, ln, pc, out, pool_fix):
        parts = ln.split(None, 1)
        mn = parts[0].lower()
        args = [a.strip() for a in parts[1].split(",")] if len(parts) > 1 else []

        def disp_of(label, scale=2):
            t = self.labels[label]
            return (t - (pc + 4)) // scale

        if mn == "nop":
            return 0x0009
        if mn == "rts":
            return 0x000B
        if mn == "sleep":
            return 0x001B
        if mn == "rte":
            return 0x002B
        if mn in ("bra", "bsr", "bt", "bf"):
            d = disp_of(args[0])
            if mn in ("bra", "bsr"):
                assert -2048 <= d < 2048, f"{mn} range"
                return (0xA000 if mn == "bra" else 0xB000) | (d & 0xFFF)
            assert -128 <= d < 128, f"{mn} {args[0]} range"
            return (0x8900 if mn == "bt" else 0x8B00) | (d & 0xFF)
        if mn in ("bt/s", "bf/s"):
            d = disp_of(args[0])
            assert -128 <= d < 128
            return (0x8D00 if mn == "bt/s" else 0x8F00) | (d & 0xFF)
        if mn == "braf":
            return 0x0023 | (R(args[0]) << 8)
        if mn == "bsrf":
            return 0x0003 | (R(args[0]) << 8)
        if mn in ("jmp", "jsr"):
            n = R(args[0].lstrip("@"))
            return (0x402B if mn == "jmp" else 0x400B) | (n << 8)
        if mn == "mov":
            if args[0].startswith("#"):
                return 0xE000 | (R(args[1]) << 8) | parse_imm(args[0][1:], -128, 127) & 0xFF
            return 0x6003 | (R(args[1]) << 8) | (R(args[0]) << 4)
        if mn == "movt":
            return 0x0029 | (R(args[0]) << 8)
        if mn in ("add", "sub", "and", "or", "xor"):
            if args[0].startswith("#"):
                if mn in ("and", "or", "xor"):     # #imm,R0 forms only
                    assert R(args[1]) == 0, f"{mn} #imm needs r0"
                    return {"and": 0xC900, "or": 0xCB00, "xor": 0xCA00}[mn] \
                        | parse_imm(args[0][1:], 0, 255) & 0xFF
                assert mn == "add", "sub takes no #imm"
                return 0x7000 | (R(args[1]) << 8) | parse_imm(args[0][1:], -128, 127) & 0xFF
            base = {"add": 0x300C, "sub": 0x3008, "and": 0x2009,
                    "or": 0x200B, "xor": 0x200A}[mn]
            return base | (R(args[1]) << 8) | (R(args[0]) << 4)
        if mn == "cmp/eq":
            if args[0].startswith("#"):
                return 0x8800 | parse_imm(args[0][1:], -128, 127) & 0xFF
            return 0x3000 | (R(args[1]) << 8) | (R(args[0]) << 4)
        if mn in ("cmp/pl", "cmp/pz"):         # single register: 4n15 / 4n11
            assert len(args) == 1, f"{mn} takes one register"
            return (0x4015 if mn == "cmp/pl" else 0x4011) | (R(args[0]) << 8)
        if mn in ("cmp/hs", "cmp/ge", "cmp/hi", "cmp/gt"):
            sub = {"cmp/hs": 2, "cmp/ge": 3, "cmp/hi": 6, "cmp/gt": 7}[mn]
            return 0x3000 | (R(args[1]) << 8) | (R(args[0]) << 4) | sub
        if mn == "cmp/str":
            return 0x200C | (R(args[1]) << 8) | (R(args[0]) << 4)
        if mn == "tst":
            if args[0].startswith("#"):
                return 0xC800 | parse_imm(args[0][1:], 0, 255)
            return 0x2008 | (R(args[1]) << 8) | (R(args[0]) << 4)
        if mn == "dt":
            # SH-4 group 0100nnnn0001mmmm: Rn is bits 8-11, the sub-op the low
            # byte (DT = 0x10). So dt r1 = 0x4110 (capstone: 0x4018 = shll8 r0
            # and 0x4011 = cmp/pz r0, i.e. register in bits 8-11).
            return 0x4010 | (R(args[0]) << 8)
        if mn in ("shll", "shlr", "shar", "rotl", "rotr", "rotcl", "rotcr",
                  "shal", "shar", "shll2", "shlr2", "shll8", "shlr8",
                  "shll16", "shlr16"):
            sub = {"shll": 0x00, "shlr": 0x01, "shar": 0x21, "shal": 0x20,
                   "rotl": 0x04, "rotr": 0x05, "rotcl": 0x24, "rotcr": 0x25,
                   "shll2": 0x08, "shlr2": 0x09, "shll8": 0x18,
                   "shlr8": 0x19, "shll16": 0x28, "shlr16": 0x29}[mn]
            return 0x4000 | (R(args[0]) << 8) | sub
        if mn in ("extu.b", "extu.w", "exts.b", "exts.w"):
            sub = {"extu.b": 0x0C, "extu.w": 0x0D, "exts.b": 0x0E, "exts.w": 0x0F}[mn]
            return 0x6000 | (R(args[1]) << 8) | (R(args[0]) << 4) | sub
        if mn in ("mov.b", "mov.w", "mov.l"):
            return self.enc_mov(mn, args, pc, out, pool_fix)
        if mn.startswith("f") or mn in ("lds", "sts") and "fpul" in ",".join(args).lower():
            v = self.enc_fpu(mn, args)
            if v is not None:
                return v
        if mn in ("muls.w", "mulu.w", "mul.l", "dmuls.l", "dmulu.l",
                  "shad", "shld", "neg"):
            base = {"muls.w": 0x200F, "mulu.w": 0x200E, "mul.l": 0x0007,
                    "dmuls.l": 0x300D, "dmulu.l": 0x3005, "shad": 0x400C,
                    "shld": 0x400D, "neg": 0x600B}[mn]
            return base | (R(args[1]) << 8) | (R(args[0]) << 4)
        if mn == "sts" and args[0].lower() in ("mach", "macl"):
            return (0x000A if args[0].lower() == "mach" else 0x001A) \
                | (R(args[1]) << 8)
        if mn == "sts.l" and args[0].lower() in ("pr", "mach", "macl"):
            # STS.L PR/MACH/MACL,@-Rn = 4n22 / 4n02 / 4n12
            m = re.match(r"(pr|mach|macl),@-(r\d+)$",
                         ",".join(args).replace(" ", "").lower())
            return {"pr": 0x4022, "mach": 0x4002, "macl": 0x4012}[m.group(1)] \
                | (R(m.group(2)) << 8)
        if mn == "lds.l":
            # LDS.L @Rm+,PR = 0x4026 (NOT 0x402A -- that is LDS Rm,PR,
            # a register move! verified by execution test 2026-09-28);
            # @Rm+,MACH = 4m06, @Rm+,MACL = 4m16
            m = re.match(r"@(r\d+)\+,(pr|mach|macl)$",
                         ",".join(args).replace(" ", "").lower())
            return {"pr": 0x4026, "mach": 0x4006, "macl": 0x4016}[m.group(2)] \
                | (R(m.group(1)) << 8)
        if mn == "mova":
            m = re.match(r"@\((.+),pc\),r0", args[0].replace(" ", ""))
            d = disp_of(m.group(1), 4)
            return 0xC700 | (d & 0xFF)
        if mn == "trapa":
            return 0xC300 | parse_imm(args[0][1:], 0, 255)
        if mn in ("ldc", "stc"):
            m = re.match(r"(r\d+),(sr|gbr|vbr|ssr|spc|sgr|dbr)", args[0] + "," + args[1])
            if not m:
                m2 = re.match(r"(sr|gbr|vbr|ssr|spc|sgr|dbr),(r\d+)", args[0] + "," + args[1])
                if mn == "ldc" or not m2:
                    raise ValueError(f"bad ldc/stc {ln}")
                reg, bank = R(m2.group(2)), m2.group(1)
                code = {"sr": 0x0E, "gbr": 0x12, "vbr": 0x22, "ssr": 0x32,
                        "spc": 0x42, "sgr": 0x3A, "dbr": 0x3E}[bank]
                return 0x4000 | (reg << 8) | code
            reg, bank = R(m.group(1)), m.group(2)
            code = {"sr": 0x0E, "gbr": 0x12, "vbr": 0x22, "ssr": 0x32,
                    "spc": 0x42, "sgr": 0x3A, "dbr": 0x3E}[bank]
            return 0x4000 | (reg << 8) | code
        raise ValueError(f"unknown: {ln}")

    # -- single-precision FPU (SH-4/4A), FPSCR.PR=0 SZ=0 only ---------------
    def enc_fpu(self, mn, args):
        def FR(x):
            x = x.strip().lower()
            m = re.fullmatch(r"fr(\d+)", x)
            if not m or int(m.group(1)) > 15:
                raise ValueError(f"bad fr reg {x!r}")
            return int(m.group(1))
        full = ",".join(args).replace(" ", "").lower()
        if mn == "lds" and args[1].strip().lower() == "fpul":
            return 0x405A | (R(args[0]) << 8)
        if mn == "sts" and args[0].strip().lower() == "fpul":
            return 0x005A | (R(args[1]) << 8)
        bin2 = {"fadd": 0, "fsub": 1, "fmul": 2, "fdiv": 3,
                "fcmp/eq": 4, "fcmp/gt": 5}
        if mn in bin2:
            return 0xF000 | (FR(args[1]) << 8) | (FR(args[0]) << 4) | bin2[mn]
        if mn == "fmac":                       # fmac fr0,frm,frn
            assert args[0].strip().lower() == "fr0"
            return 0xF00E | (FR(args[2]) << 8) | (FR(args[1]) << 4)
        if mn == "fmov" or mn == "fmov.s":
            m = re.fullmatch(r"fr(\d+),fr(\d+)", full)
            if m:
                return 0xF00C | (int(m.group(2)) << 8) | (int(m.group(1)) << 4)
            pats = [
                (r"@\(r0,(r\d+)\),fr(\d+)", 6, lambda a, b: (b, a)),
                (r"fr(\d+),@\(r0,(r\d+)\)", 7, lambda a, b: (int(b[1:]), int(a))),
                (r"@(r\d+)\+,fr(\d+)", 9, lambda a, b: (b, a)),
                (r"@(r\d+),fr(\d+)", 8, lambda a, b: (b, a)),
                (r"fr(\d+),@-(r\d+)", 11, lambda a, b: (int(b[1:]), int(a))),
                (r"fr(\d+),@(r\d+)", 10, lambda a, b: (int(b[1:]), int(a))),
            ]
            for rx, sub, _ in pats:
                m = re.fullmatch(rx, full)
                if m:
                    g = m.groups()
                    if g[0].startswith("r"):        # @Rm.. ,FRn
                        n, mm = int(g[1]), int(g[0][1:])
                    else:                           # FRm,@Rn
                        mm, n = int(g[0]), int(g[1][1:])
                    return 0xF000 | (n << 8) | (mm << 4) | sub
            raise ValueError(f"bad fmov {full}")
        un = {"fsts": 0x0D, "flds": 0x1D, "float": 0x2D, "ftrc": 0x3D,
              "fneg": 0x4D, "fabs": 0x5D, "fsqrt": 0x6D, "fsrra": 0x7D,
              "fldi0": 0x8D, "fldi1": 0x9D}
        if mn in un:
            if mn == "fsts":                    # fsts fpul,frn
                return 0xF000 | (FR(args[1]) << 8) | 0x0D
            if mn in ("flds",):                 # flds frm,fpul
                return 0xF000 | (FR(args[0]) << 8) | 0x1D
            if mn == "float":                   # float fpul,frn
                return 0xF000 | (FR(args[1]) << 8) | 0x2D
            if mn == "ftrc":                    # ftrc frm,fpul
                return 0xF000 | (FR(args[0]) << 8) | 0x3D
            return 0xF000 | (FR(args[0]) << 8) | un[mn]
        if mn == "fsca":                        # fsca fpul,drn (n even fr)
            n = FR(args[1].replace("dr", "fr"))
            return 0xF0FD | (n << 8)
        return None

    def enc_mov(self, mn, args, pc, out, pool_fix):
        size = {"mov.b": 0, "mov.w": 1, "mov.l": 2}[mn]
        full = ",".join(args).replace(" ", "")   # commas inside (..) rejoined
        # mov.l @LABEL,Rn (auto pool)
        m = re.match(r"@\(([A-Za-z_][\w]*|0[xX][0-9a-fA-F]+|\d+),pc\),(r\d+)$", full)
        if m and mn == "mov.l":
            n = R(m.group(2))
            pool_fix.append([len(out), pc, m.group(1), n])
            out += b"\x00\x00"  # placeholder, backpatched
            return None
        # Rm,@-Rn
        m = re.match(r"(r\d+),@-(r\d+)$", full)
        if m:
            return 0x2000 | (R(m.group(2)) << 8) | (R(m.group(1)) << 4) | (0x4 + size)
        # @Rm+,Rn
        m = re.match(r"@(r\d+)\+,(r\d+)$", full)
        if m:
            return 0x6000 | (R(m.group(2)) << 8) | (R(m.group(1)) << 4) | (0x4 + size)
        # @(R0,Rm),Rn / Rm,@(R0,Rn)   (0000 nnnn mmmm 11ss / 0000 nnnn mmmm 01ss)
        m = re.match(r"@\(r0,(r\d+)\),(r\d+)$", full)
        if m:
            return 0x000C | (R(m.group(2)) << 8) | (R(m.group(1)) << 4) | size
        m = re.match(r"(r\d+),@\(r0,(r\d+)\)$", full)
        if m:
            return 0x0004 | (R(m.group(2)) << 8) | (R(m.group(1)) << 4) | size
        # @Rm,Rn -- sub 0/1/2 (B/W/L), same as the Rm,@Rn stores below and
        # as capstone. (Earlier 0xC+size here assembled to extu/exts!)
        m = re.match(r"@(r\d+),(r\d+)$", full)
        if m:
            return 0x6000 | (R(m.group(2)) << 8) | (R(m.group(1)) << 4) | size
        # Rm,@Rn -- same convention: sub 0/1/2.
        m = re.match(r"(r\d+),@(r\d+)$", full)
        if m:
            return 0x2000 | (R(m.group(2)) << 8) | (R(m.group(1)) << 4) | size
        # @(disp,Rm),Rn -- byte/word forms (0x84/0x85) are R0-destination
        # only; mov.l (0101 nnnn mmmm dddd) takes any Rn.
        m = re.match(r"@\((\d+),(r\d+)\),(r\d+)$", full)
        if m:
            d, mm, n = int(m.group(1)), R(m.group(2)), R(m.group(3))
            if n != 0 and size < 2:
                raise ValueError(
                    f"mov.{'bwl'[size]} @({d},{mm}),{n}: SH-4 has no such "
                    f"form (byte/word loads go to R0 only); use add+@{n}")
            if size == 0:
                assert d < 16
                return 0x8400 | (n << 8) | (mm << 4) | d
            if size == 1:
                assert d % 2 == 0 and d // 2 < 16
                return 0x8500 | (n << 8) | (mm << 4) | (d // 2)
            assert d % 4 == 0 and d // 4 < 16
            return 0x5000 | (n << 8) | (mm << 4) | (d // 4)
        # Rm,@(disp,Rn)
        m = re.match(r"(r\d+),@\((\d+),(r\d+)\)$", full)
        if m:
            mm, d, n = R(m.group(1)), int(m.group(2)), R(m.group(3))
            if size < 2 and mm != 0:
                raise ValueError("mov.b/w Rm,@(disp,Rn): source must be R0")
            if size == 0:                       # 1000 0000 nnnn dddd
                assert d < 16
                return 0x8000 | (n << 4) | d
            if size == 1:                       # 1000 0001 nnnn dddd
                assert d % 2 == 0 and d // 2 < 16
                return 0x8100 | (n << 4) | (d // 2)
            assert d % 4 == 0 and d // 4 < 16
            return 0x1000 | (n << 8) | (mm << 4) | (d // 4)
        # @(disp,GBR),R0 / R0,@(disp,GBR)
        m = re.match(r"@\((\d+),gbr\),r0$", full)
        if m:
            d = int(m.group(1))
            if size == 0:
                assert d < 256
                return 0xC400 | d
            if size == 1:
                assert d % 2 == 0 and d // 2 < 256
                return 0xC500 | (d // 2)
            assert d % 4 == 0 and d // 4 < 256
            return 0xC600 | (d // 4)
        m = re.match(r"r0,@\((\d+),gbr\)$", full)
        if m:
            d = int(m.group(1))
            if size == 0:
                assert d < 256
                return 0xC000 | d
            if size == 1:
                assert d % 2 == 0 and d // 2 < 256
                return 0xC100 | (d // 2)
            assert d % 4 == 0 and d // 4 < 256
            return 0xC200 | (d // 4)
        raise ValueError(f"mov form unimpl: {mn} {args}")


def assemble(src):
    return Asm().assemble(src)


if __name__ == "__main__":
    src = open(sys.argv[1]).read()
    out = assemble(src)
    if "-o" in sys.argv:
        open(sys.argv[sys.argv.index("-o") + 1], "wb").write(out)
    else:
        sys.stdout.buffer.write(out)
