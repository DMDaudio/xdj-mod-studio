#!/usr/bin/env python3
"""fft_kernel_test.py -- one SH-4 butterfly vs the integer model.

Assembles a single DIT butterfly (muls.w + shld + adds) with shasm,
runs N random cases in sh4box, compares word-for-word with fft_fixed.
"""
import random
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code, ENTRY
from fft_fixed import s32

SRC = """
bfly:
  muls.w r6, r8
  sts macl, r10
  muls.w r7, r9
  sts macl, r11
  sub r11, r10
  muls.w r6, r9
  sts macl, r11
  muls.w r7, r8
  sts macl, r12
  add r12, r11
  mov #-15, r13
  shad r13, r10
  shad r13, r11
  mov r4, r0
  add r10, r0
  shar r0
  mov r5, r1
  add r11, r1
  shar r1
  mov r4, r2
  sub r10, r2
  shar r2
  mov r5, r3
  sub r11, r3
  shar r3
  rts
  nop
"""


def model(a, b, c, d, wr, wi):
    tr = s32((s32(c * wr - d * wi)) >> 15)
    ti = s32((s32(c * wi + d * wr)) >> 15)
    return (s32((s32(a + tr)) >> 1) & 0xFFFFFFFF,
            s32((s32(b + ti)) >> 1) & 0xFFFFFFFF,
            s32((s32(a - tr)) >> 1) & 0xFFFFFFFF,
            s32((s32(b - ti)) >> 1) & 0xFFFFFFFF)


def u32(v):
    return v & 0xFFFFFFFF


def main():
    code = assemble(SRC)
    print(f"assembled {len(code)} bytes")
    rnd = random.Random(7)
    bad = 0
    for t in range(64):
        vals = [rnd.randint(-32768, 32767) for _ in range(6)]
        a, b, c, d, wr, wi = vals
        regs = {4: u32(a), 5: u32(b), 6: u32(c), 7: u32(d),
                8: u32(wr), 9: u32(wi)}
        cpu, _, res = run_code(code, regs)
        got = tuple(u32(cpu.gr(i)) for i in range(4))
        want = model(a, b, c, d, wr, wi)
        # gr() returns signed; compare as u32
        if got != want:
            bad += 1
            if bad < 4:
                print(f"MISMATCH in={vals} got={[hex(g) for g in got]} "
                      f"want={[hex(w) for w in want]}")
    print("RESULT:", "PASS" if bad == 0 else f"FAIL {bad}/64")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
