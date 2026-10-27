#!/usr/bin/env python3
"""a64enc_test.py -- check every emu/a64enc.h encoder against the system
assembler (clang). Exit 0 = all words identical."""
import os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
CASES = [  # (C expression, assembly)
    ("a_addw(1,2,3)", "add w1, w2, w3"), ("a_subw(4,5,6)", "sub w4, w5, w6"),
    ("a_andw(7,8,9)", "and w7, w8, w9"), ("a_orrw(10,11,12)", "orr w10, w11, w12"),
    ("a_eorw(13,14,15)", "eor w13, w14, w15"), ("a_movw(3,4)", "mov w3, w4"),
    ("a_mvnw(5,6)", "mvn w5, w6"), ("a_negw(7,8)", "neg w7, w8"),
    ("a_cmpw(1,2)", "cmp w1, w2"), ("a_tstw(3,4)", "tst w3, w4"),
    ("a_mulw(1,2,3)", "mul w1, w2, w3"), ("a_lslvw(1,2,3)", "lsl w1, w2, w3"),
    ("a_lsrvw(1,2,3)", "lsr w1, w2, w3"), ("a_asrvw(1,2,3)", "asr w1, w2, w3"),
    ("a_cmpx(6,22)", "cmp x6, x22"), ("a_movx(0,19)", "mov x0, x19"),
    ("a_addx_lsl(2,20,1,5)", "add x2, x20, x1, lsl #5"),
    ("a_csetw(4,A_EQ)", "cset w4, eq"), ("a_csetw(4,A_HS)", "cset w4, hs"),
    ("a_csetw(4,A_GE)", "cset w4, ge"), ("a_csetw(4,A_HI)", "cset w4, hi"),
    ("a_csetw(4,A_GT)", "cset w4, gt"), ("a_csetw(4,A_NE)", "cset w4, ne"),
    ("a_csetw(4,A_LT)", "cset w4, lt"),
    ("a_cselw(1,2,3,A_NE)", "csel w1, w2, w3, ne"),
    ("a_addiw(1,2,4095)", "add w1, w2, #4095"), ("a_subiw(1,2,4)", "sub w1, w2, #4"),
    ("a_cmpiw(3,0)", "cmp w3, #0"), ("a_cmpiw(3,255)", "cmp w3, #255"),
    ("a_lslw(1,2,1)", "lsl w1, w2, #1"), ("a_lslw(1,2,2)", "lsl w1, w2, #2"),
    ("a_lslw(1,2,8)", "lsl w1, w2, #8"), ("a_lslw(1,2,16)", "lsl w1, w2, #16"),
    ("a_lsrw(1,2,1)", "lsr w1, w2, #1"), ("a_lsrw(1,2,12)", "lsr w1, w2, #12"),
    ("a_lsrw(1,2,31)", "lsr w1, w2, #31"), ("a_asrw(1,2,1)", "asr w1, w2, #1"),
    ("a_uxtbw(1,2)", "uxtb w1, w2"), ("a_uxthw(1,2)", "uxth w1, w2"),
    ("a_sxtbw(1,2)", "sxtb w1, w2"), ("a_sxthw(1,2)", "sxth w1, w2"),
    ("a_ubfxw(1,0,12,8)", "ubfx w1, w0, #12, #8"), ("a_bfi0w(2,3)", "bfi w2, w3, #0, #1"),
    ("a_andiw_ones(5,0,20,20)", "and w5, w0, #0xfffff000"),
    ("a_andiw_ones(7,0,12,0)", "and w7, w0, #0xfff"),
    ("a_orriw_ones(5,5,1,0)", "orr w5, w5, #1"), ("a_orriw_ones(5,5,1,31)", "orr w5, w5, #2"),
    ("a_orriw_ones(5,5,2,0)", "orr w5, w5, #3"),
    ("a_tstiw_ones(0,1,0)", "tst w0, #1"), ("a_tstiw_ones(0,2,0)", "tst w0, #3"),
    ("a_tstiw_ones(2,1,0)", "tst w2, #1"),
    ("a_movzw(1,0x1234,0)", "movz w1, #0x1234"), ("a_movkw(1,0xABCD,1)", "movk w1, #0xabcd, lsl #16"),
    ("a_movzx(16,0x1234,0)", "movz x16, #0x1234"), ("a_movkx(16,0xBEEF,2)", "movk x16, #0xbeef, lsl #32"),
    ("a_movkx(16,0xFEED,3)", "movk x16, #0xfeed, lsl #48"),
    ("a_ldrw_i(1,19,64)", "ldr w1, [x19, #64]"), ("a_strw_i(1,19,124)", "str w1, [x19, #124]"),
    ("a_ldrx_i(6,2,8)", "ldr x6, [x2, #8]"), ("a_strx_i(6,2,16)", "str x6, [x2, #16]"),
    ("a_ldrhw_i(8,2,16)", "ldrh w8, [x2, #16]"), ("a_ldpw(3,4,2,0)", "ldp w3, w4, [x2]"),
    ("a_ldrw_r(0,6,7)", "ldr w0, [x6, x7]"), ("a_ldrhw_r(0,6,7)", "ldrh w0, [x6, x7]"),
    ("a_ldrbw_r(0,6,7)", "ldrb w0, [x6, x7]"), ("a_strw_r(1,6,7)", "str w1, [x6, x7]"),
    ("a_strhw_r(1,6,7)", "strh w1, [x6, x7]"), ("a_strbw_r(1,6,7)", "strb w1, [x6, x7]"),
    ("a_stpx_pre(29,30,-48)", "stp x29, x30, [sp, #-48]!"),
    ("a_ldpx_post(29,30,48)", "ldp x29, x30, [sp], #48"),
    ("a_stpx(19,20,31,16)", "stp x19, x20, [sp, #16]"), ("a_ldpx(21,22,31,32)", "ldp x21, x22, [sp, #32]"),
    ("a_mov_fp_sp()", "mov x29, sp"), ("a_blr(16)", "blr x16"), ("a_ret()", "ret"),
    ("a_br(16)", "br x16"), ("a_br(1)", "br x1"),
    ("a_addix(10,24,33)", "add x10, x24, #33"), ("a_cmpix(10,0)", "cmp x10, #0"),
    ("a_maddx(9,9,11,10)", "madd x9, x9, x11, x10"), ("a_ldrbw_i(10,9,21)", "ldrb w10, [x9, #21]"),
    ("a_strx_i(2,31,96)", "str x2, [sp, #96]"), ("a_ldrx_i(2,31,96)", "ldr x2, [sp, #96]"),
    ("a_stpx(24,25,2,0)", "stp x24, x25, [x2]"), ("a_ldpx(26,27,2,16)", "ldp x26, x27, [x2, #16]"),
    ("a_stpx_pre(29,30,-112)", "stp x29, x30, [sp, #-112]!"), ("a_ldpx_post(29,30,112)", "ldp x29, x30, [sp], #112"),
    ("a_andiw_ones(9,24,6,0)", "and w9, w24, #63"), ("a_andiw_ones(9,23,29,0)", "and w9, w23, #0x1fffffff"),
    ("a_ubfxw(9,23,1,16)", "ubfx w9, w23, #1, #16"), ("a_tstiw_ones(23,1,0)", "tst w23, #1"),
    ("a_cmpw(10,23)", "cmp w10, w23"),
]
BRANCH = [("a_b(3)", "b .+12"), ("a_b(-2)", "b .-8"), ("a_bcc(A_NE,5)", "b.ne .+20"),
          ("a_bcc(A_EQ,-3)", "b.eq .-12"), ("a_cbzw(4,2)", "cbz w4, .+8"),
          ("a_cbnzw(4,-1)", "cbnz w4, .-4"), ("a_cbzx(16,3)", "cbz x16, .+12")]


def main():
    cases = CASES + BRANCH
    with tempfile.TemporaryDirectory() as td:
        asm = os.path.join(td, "t.s")
        with open(asm, "w") as f:
            f.write(".text\n.globl _t\n_t:\n")
            for _, a in cases:
                f.write(f"  {a}\n")
        obj = os.path.join(td, "t.o")
        subprocess.check_call(["clang", "-c", "-arch", "arm64", asm, "-o", obj])
        dis = subprocess.run(["otool", "-tXv", obj], capture_output=True, text=True).stdout
        words = subprocess.run(["otool", "-tX", obj], capture_output=True, text=True).stdout
        ref = []
        for line in words.splitlines():
            parts = line.split()
            if len(parts) > 1 and re.fullmatch(r"[0-9a-f]{16}", parts[0]):
                ref += [int(w, 16) for w in parts[1:]]
        src = os.path.join(td, "e.c")
        with open(src, "w") as f:
            f.write(f'#include "{os.path.join(ROOT, "emu", "a64enc.h")}"\n#include <stdio.h>\n')
            f.write("int main(void){\n")
            for e, _ in cases:
                f.write(f'  printf("%08x\\n", (unsigned)({e}));\n')
            f.write("  return 0;}\n")
        exe = os.path.join(td, "e")
        subprocess.check_call(["cc", "-o", exe, src])
        mine = [int(x, 16) for x in subprocess.run([exe], capture_output=True, text=True).stdout.split()]
    bad = 0
    for (e, a), r, m in zip(cases, ref, mine):
        if r != m:
            bad += 1
            print(f"MISMATCH {e:28s} {a:32s} asm {r:08x} mine {m:08x}")
    print(f"{len(cases)} encodings, {bad} mismatches")
    print("RESULT:", "PASS" if not bad and len(ref) == len(cases) else "FAIL")
    return 1 if bad or len(ref) != len(cases) else 0


if __name__ == "__main__":
    sys.exit(main())
