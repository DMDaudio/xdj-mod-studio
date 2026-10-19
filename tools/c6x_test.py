#!/usr/bin/env python3
"""c6x_test.py -- unit tests for the C674x core (emu/c6x/cpu.py + isa.py).

Each test is a snippet assembled with GNU as for tic6x (work/tic6x-tools,
built from the local binutils 2.43.1 source), linked at 0x11800000 (DSP
L2), decoded by emu/c6x/decode.py and run until pc reaches the `halt`
label. Expected values come from the TI manual (SPRUFE8B) worked examples
and its timing rules (delay slots, branch shadow, SPLOOP flow tables).
  .venv/bin/python tools/c6x_test.py          -> all tests, PASS/FAIL each
"""
import os
import subprocess
import sys
import tempfile
sys.path.insert(0, ".")
from emu.c6x.cpu import Cpu, REGIDX
from emu.c6x.decode import decode_bytes

T = "work/tic6x-tools/bin/tic6x-elf-"
BASE = 0x11800000


class Mem:
    def __init__(self):
        self.b = bytearray(0x40000)
        self.log = []

    def read(self, a, n):
        o = a - BASE
        if 0 <= o < len(self.b):
            return int.from_bytes(self.b[o:o + n], "little")
        self.log.append(("r", a, n))
        return 0

    def write(self, a, n, v):
        o = a - BASE
        if 0 <= o < len(self.b):
            self.b[o:o + n] = (v & ((1 << (8 * n)) - 1)).to_bytes(n, "little")
        else:
            self.log.append(("w", a, n, v))


def build(src):
    d = tempfile.mkdtemp()
    s, o, e, b = (os.path.join(d, x) for x in ("t.s", "t.o", "t.elf", "t.bin"))
    open(s, "w").write("\t.text\n\t.global _start\n_start:\n" + src +
                       "\nhalt:\n\tnop\n\tnop\n\tnop\n\tnop\n\tnop\n\tnop\n\tnop\n\tnop\n")
    subprocess.run([T + "as", "-march=c674x", "-mlittle-endian", "-o", o, s], check=True)
    subprocess.run([T + "ld", "-Ttext", hex(BASE), "-e", "_start", "-o", e, o], check=True)
    subprocess.run([T + "objcopy", "-O", "binary", "-j", ".text", e, b], check=True)
    syms = subprocess.run([T + "nm", e], capture_output=True, text=True).stdout
    halt = next(int(l.split()[0], 16) for l in syms.splitlines() if l.endswith(" halt"))
    return open(b, "rb").read(), halt


def run(src, init=None, maxc=5000):
    blob, halt = build(src)
    mem = Mem()
    mem.b[:len(blob)] = blob
    for a, v in (init or {}).items():
        mem.write(a, 4, v)
    if os.environ.get("XDJ_DSP_CORE", "py") == "c":
        from emu.c6x.cfast import CCpu
        cpu = CCpu(mem)
    else:
        cpu = Cpu(mem)
    cpu.add_code(decode_bytes(blob + bytes(64), BASE))
    cpu.pc = BASE
    cpu.run(maxc, stop=lambda c: c.pc == halt and c.sp is None and c.branch_at is None)
    cpu.run(10)                  # let in-flight writes land (loads E5, stores E3)
    return cpu, mem


def R(cpu, n):
    return cpu.r[REGIDX[n]]


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   ({detail})" if detail and not cond else ""))


def t_delay_slots():
    cpu, _ = run("""
        mvk .s1 3, a1
        mvk .s1 4, a2
        nop
        mpy .m1 a1, a2, a3      ; 1 delay slot
        mv .l1 a3, a4           ; old a3 (0)
        mv .l1 a3, a5           ; 12
        mpy32 .m1 a1, a2, a6    ; 3 delay slots
        mv .l1 a6, a7           ; 0
        mv .l1 a6, a8           ; 0
        mv .l1 a6, a9           ; 0
        mv .l1 a6, a10          ; 12
    """)
    check("MPY 1 delay slot", R(cpu, "a4") == 0 and R(cpu, "a5") == 12,
          f"a4={R(cpu, 'a4')} a5={R(cpu, 'a5')}")
    check("MPY32 3 delay slots", [R(cpu, f"a{k}") for k in (7, 8, 9, 10)] == [0, 0, 0, 12])


def t_load_delay():
    cpu, _ = run(f"""
        mvkl .s1 data, a1
        mvkh .s1 data, a1
        nop 5
        ldw .d1t1 *a1, a2       ; 4 delay slots
        mv .l1 a2, a3
        mv .l1 a2, a4
        mv .l1 a2, a5
        mv .l1 a2, a6
        mv .l1 a2, a7           ; loaded value
        b .s1 halt
        nop 5
data:   .word 0x12345678
    """)
    check("LDW 4 delay slots", [R(cpu, f"a{k}") for k in range(3, 8)] == [0, 0, 0, 0, 0x12345678],
          str([hex(R(cpu, f"a{k}")) for k in range(3, 8)]))


def t_branch_shadow():
    cpu, _ = run("""
        b .s1 tgt
        mvk .s1 1, a1
        mvk .s1 2, a2
        mvk .s1 3, a3
        mvk .s1 4, a4
        mvk .s1 5, a5
        mvk .s1 6, a6           ; not executed
tgt:    mvk .s1 7, a7
        b .s1 halt
        nop 5
    """)
    check("branch: exactly 5 delay slots", [R(cpu, f"a{k}") for k in range(1, 8)] == [1, 2, 3, 4, 5, 0, 7],
          str([R(cpu, f"a{k}") for k in range(1, 8)]))


def t_parallel_reads():
    cpu, _ = run("""
        mvk .s1 5, a1
        mvk .s2 9, b1
        nop
        mv .l1x b1, a1
     || mv .l2x a1, b1          ; swap: both read before either writes
        nop
    """)
    check("parallel EP reads before writes", R(cpu, "a1") == 9 and R(cpu, "b1") == 5)


def t_bitfields():
    cpu, _ = run("""
        mvkl .s1 0x1234abcd, a1
        mvkh .s1 0x1234abcd, a1
        nop
        ext .s1 a1, 16, 24, a2     ; ((a1 << 16) as signed) >> 24
        extu .s1 a1, 16, 24, a3
        clr .s1 a1, 4, 19, a4
        set .s1 a1, 4, 19, a5
        nop
    """)
    a1 = 0x1234ABCD
    e = ((a1 << 16) & 0xFFFFFFFF)
    ext = ((e - (1 << 32) if e & 0x80000000 else e) >> 24) & 0xFFFFFFFF
    check("EXT/EXTU/CLR/SET", (R(cpu, "a2"), R(cpu, "a3"), R(cpu, "a4"), R(cpu, "a5")) ==
          (ext, e >> 24, a1 & ~0x000FFFF0 & 0xFFFFFFFF, a1 | 0x000FFFF0),
          str([hex(R(cpu, f"a{k}")) for k in (2, 3, 4, 5)]))


SRCBUF, DSTBUF = BASE + 0x1000, BASE + 0x2000


def copy_check(name, cpu, mem, n):
    got = [mem.read(DSTBUF + 4 * k, 4) for k in range(n + 2)]
    want = [0x100 + k for k in range(n)] + [0, 0]
    check(name, got == want, str([hex(v) for v in got]))


def init_src():
    return {SRCBUF + 4 * k: 0x100 + k for k in range(12)}


def t_sploop_7_4():
    cpu, mem = run(f"""
        mvkl .s1 {SRCBUF:#x}, a1
        mvkh .s1 {SRCBUF:#x}, a1
        mvkl .s2 {DSTBUF:#x}, b0
        mvkh .s2 {DSTBUF:#x}, b0
        mvk .s2 8, b4
        mvc .s2 b4, ilc
        nop 3
        sploop 1
        ldw .d1t1 *a1++, a2
        nop 4
        mv .l2x a2, b2
        spkernel 6, 0
     || stw .d2t2 b2, *b0++
        nop 2
    """, init_src())
    copy_check("SPLOOP (Example 7-4): exactly 8 words copied", cpu, mem, 8)


def t_sploopd_7_5():
    cpu, mem = run(f"""
        mvkl .s1 {SRCBUF:#x}, a1
        mvkh .s1 {SRCBUF:#x}, a1
        mvkl .s2 {DSTBUF:#x}, b0
        mvkh .s2 {DSTBUF:#x}, b0
        mvk .s2 4, b4
        nop
        sploopd 1
     || mvc .s2 b4, ilc
        ldw .d1t1 *a1++, a2
        nop 4
        mv .l2x a2, b2
        spkernel 6, 0
     || stw .d2t2 b2, *b0++
        nop 2
    """, init_src())
    copy_check("SPLOOPD (Example 7-5): exactly 8 words copied", cpu, mem, 8)


def t_sploopw_7_7():
    cpu, mem = run(f"""
        mvkl .s1 {SRCBUF:#x}, a1
        mvkh .s1 {SRCBUF:#x}, a1
        mvkl .s2 {DSTBUF:#x}, b0
        mvkh .s2 {DSTBUF:#x}, b0
        mvk .s1 8, a0
        nop
        [a0] sploopw 1
        ldw .d1t1 *a1++, a2
        nop 1
        sub .s1 a0, 1, a0
        nop 2
        mv .l2x a2, b2
        spkernel 0, 0
     || stw .d2t2 b2, *b0++
        nop 2
    """, init_src())
    copy_check("SPLOOPW (Example 7-7): exactly 8 words copied", cpu, mem, 8)


def t_sploop_zero_trip():
    cpu, mem = run(f"""
        mvkl .s1 {SRCBUF:#x}, a1
        mvkh .s1 {SRCBUF:#x}, a1
        mvkl .s2 {DSTBUF:#x}, b0
        mvkh .s2 {DSTBUF:#x}, b0
        mvk .s2 0, b4
        mvc .s2 b4, ilc
        nop 3
        sploop 1
        ldw .d1t1 *a1++, a2
        nop 4
        mv .l2x a2, b2
        spkernel 6, 0
     || stw .d2t2 b2, *b0++
        mvk .s1 77, a9
        nop 2
    """, init_src())
    check("SPLOOP ILC=0 (§7.9.1): body inert, code after runs",
          mem.read(DSTBUF, 4) == 0 and R(cpu, "a9") == 77 and R(cpu, "a1") == SRCBUF)


def t_sploop_early_exit():
    ok = True
    for n in (1, 2, 3):
        cpu, mem = run(f"""
        mvkl .s1 {SRCBUF:#x}, a1
        mvkh .s1 {SRCBUF:#x}, a1
        mvkl .s2 {DSTBUF:#x}, b0
        mvkh .s2 {DSTBUF:#x}, b0
        mvk .s2 {n}, b4
        mvc .s2 b4, ilc
        nop 3
        sploop 1
        ldw .d1t1 *a1++, a2
        nop 4
        mv .l2x a2, b2
        spkernel 6, 0
     || stw .d2t2 b2, *b0++
        mvk .s1 77, a9
        nop 2
        """, init_src())
        got = [mem.read(DSTBUF + 4 * k, 4) for k in range(n + 2)]
        ok &= got == [0x100 + k for k in range(n)] + [0, 0] and R(cpu, "a9") == 77
    check("SPLOOP early exit (§7.8.2): ILC=1/2/3 copy exactly 1/2/3", ok)


def t_branch_in_delay_slot():
    """A branch issued in an earlier branch's delay slots (legal C6000;
    Pioneer's DSP image calls this way): the first target runs for the 2
    cycles between the landings, then the second lands. The old single
    pending-branch slot lost the first branch and ran the fall-through."""
    cpu, mem = run("""
        mvk .S1 0,a1
        mvk .S1 0,a2
        mvk .S1 0,a3
        b .S1 l1
        nop 1
        b .S2 halt
        nop 3
        mvk .S1 9,a3
    l1: mvk .S1 1,a1
        mvk .S1 2,a2
        mvk .S1 5,a2
        nop 5
        """)
    check("branch in a branch's delay slots: both land in order",
          (R(cpu, "a1"), R(cpu, "a2"), R(cpu, "a3")) == (1, 2, 0),
          f"a1 a2 a3 = {R(cpu, 'a1')} {R(cpu, 'a2')} {R(cpu, 'a3')}")


def t_compact_pred_dst():
    """Compact .L compares (L2c / Lx3c / Lx1c) can only write A0/A1/B0/B1;
    the fetch-packet header's RS bit moves their sources to A16-A23/B16-B23
    but not the destination (SPRUFE8B figs D-7, D-9, D-10). objdump adds 16
    anyway (b17 for b1); Pioneer's PCM unpacker builds load predicates this
    way, and the wrong register read address 0x2 (2026-10-18)."""
    import struct
    # word 0 = two 16-bit insns: 0x2927 'cmpeq .L2 1,src2,b1' (Lx3c) and
    # 0x2026 'cmpeq .L1 1,src2,a0'; words 1-6 = nop; header: word 0 compact
    # (layout bit 21), RS = 1 (bit 19)
    words = [0x2927 | 0x2026 << 16] + [0] * 6 + [0xE0000000 | 1 << 21 | 1 << 19]
    ins = decode_bytes(struct.pack("<8I", *words), BASE)
    a, b = ins.get(BASE), ins.get(BASE + 2)
    got = (a.mnem, a.ops, b.mnem, b.ops) if a and b else None
    check("compact cmpeq in an RS=1 packet writes b1 / a0 (not b17 / a16)",
          got == ("cmpeq", ["1", "b18", "b1"], "cmpeq", ["1", "a16", "a0"]),
          f"got {got}")


if __name__ == "__main__":
    for t in (t_delay_slots, t_load_delay, t_branch_shadow, t_parallel_reads,
              t_bitfields, t_sploop_7_4, t_sploopd_7_5, t_sploopw_7_7,
              t_sploop_zero_trip, t_sploop_early_exit, t_branch_in_delay_slot,
              t_compact_pred_dst):
        try:
            t()
        except Exception as e:
            check(t.__name__, False, f"{type(e).__name__}: {e}")
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)
