#!/usr/bin/env python3
"""pv_core_test.py -- bit-check the SH-4 key-shift core against pv_fixed.py.

Assembles each kernel with shasm, runs it on a fresh emulated SH-4
(sh4box) and compares word for word with the integer model.
Kernels so far: resample_q16 (s16 in/out).
"""
import math
import struct
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code
import pv_fixed as M

RESAMPLE = """
resample:
  mov.l r8, @-r15
  mov.l r9, @-r15
  tst r6, r6
  bt done
  mov #-15, r9
  mov #0, r1
loop:
  mov r1, r2
  shlr16 r2
  shll r2
  mov r2, r0
  mov.w @(r0,r5), r3
  add #2, r0
  mov.w @(r0,r5), r8
  sub r3, r8
  mov r1, r2
  extu.w r2, r2
  shlr r2
  mul.l r2, r8
  sts macl, r8
  shad r9, r8
  add r3, r8
  mov.w r8, @r4
  add #2, r4
  add r7, r1
  dt r6
  bf loop
done:
  mov.l @r15+, r9
  mov.l @r15+, r8
  rts
  nop
"""

OLA_ADD = """
ola_add:
  tst r7, r7
  bt done
  mov #-15, r0
loop:
  mov.w @r5+, r1
  mov.w @r6+, r2
  muls.w r1, r2
  sts macl, r2
  shad r0, r2
  mov.l @r4, r3
  add r2, r3
  mov.l r3, @r4
  add #4, r4
  dt r7
  bf loop
done:
  rts
  nop
"""

FFT = """
fft:
  mov.l r8, @-r15
  mov.l r9, @-r15
  mov.l r10, @-r15
  mov.l r11, @-r15
  mov.l r12, @-r15
  mov.l r13, @-r15
  mov.l r14, @-r15
  add #-20, r15
  mov.l r7, @(16,r15)
  mov.l r4, @r15
  mov.l r5, @(4,r15)
  mov r6, r7
  shll2 r7
  mov r4, r1
  add r7, r1
  mov.l r1, @(8,r15)
  mov r7, r10
  shlr r10
  mov.l r10, @(12,r15)
  mov #4, r9
  mov #-15, r6
stage:
  mov.l @r15, r1
  mov r1, r2
  add r9, r2
block:
  mov r15, r3
  add #4, r3
  mov.l @r3, r3
  mov r9, r8
  shlr2 r8
inner:
  mov #2, r0
  mov.w @r2, r11
  mov.w @(r0,r2), r12
  mov.w @r3, r13
  mov.w @(r0,r3), r14
  muls.w r11, r13
  sts macl, r4
  muls.w r12, r14
  sts macl, r5
  sub r5, r4
  shad r6, r4
  muls.w r11, r14
  sts macl, r5
  muls.w r12, r13
  sts macl, r7
  add r7, r5
  shad r6, r5
  mov r15, r7
  add #16, r7
  mov.l @r7, r7
  mov.w @r1, r11
  mov.w @(r0,r1), r12
  mov r11, r13
  add r4, r13
  shad r7, r13
  mov.w r13, @r1
  mov r12, r14
  add r5, r14
  shad r7, r14
  mov.w r14, @(r0,r1)
  sub r4, r11
  shad r7, r11
  mov.w r11, @r2
  sub r5, r12
  shad r7, r12
  mov.w r12, @(r0,r2)
  add #4, r1
  add #4, r2
  add r10, r3
  dt r8
  bf inner
  mov r2, r1
  add r9, r2
  mov r15, r11
  add #8, r11
  mov.l @r11, r11
  cmp/hs r11, r1
  bf block
  mov r15, r11
  add #12, r11
  mov.l @r11, r11
  cmp/eq r11, r9
  bt done
  shll r9
  shlr r10
  bra stage
  nop
done:
  add #20, r15
  mov.l @r15+, r14
  mov.l @r15+, r13
  mov.l @r15+, r12
  mov.l @r15+, r11
  mov.l @r15+, r10
  mov.l @r15+, r9
  mov.l @r15+, r8
  rts
  nop
"""

IN_ADDR, OUT_ADDR, STACK = 0x0B200000, 0x0B300000, 0x0B3F0000


def test_resample(step, n=4000, seed=1):
    import random
    rnd = random.Random(seed)
    x = [int(12000 * math.sin(2 * math.pi * 440 * i / 44100)) +
         rnd.randint(-3000, 3000) for i in range(n)]
    n_out = ((n - 1) << 16) // step
    want = M.resample_q16(x, step, n_out)
    code = assemble(RESAMPLE)
    data = {IN_ADDR: struct.pack(f"<{n}h", *x)}
    c, mem, res = run_code(code, regs={4: OUT_ADDR, 5: IN_ADDR, 6: n_out,
                                       7: step, 15: STACK}, data=data,
                           limit=5_000_000)
    got = list(struct.unpack(f"<{n_out}h", bytes(mem.r8(OUT_ADDR + i)
                                                 for i in range(n_out * 2))))
    bad = sum(1 for a, b in zip(got, want) if a != b)
    print(f"step={step / 65536:.4f} n_out={n_out} mismatches={bad}  ({res})")
    return bad == 0


def test_ola(n=2048, hop=512, frames=6, seed=2):
    import random
    rnd = random.Random(seed)
    win = M.hann_q15(n)
    code = assemble(OLA_ADD)
    total = (frames - 1) * hop + n
    want = [0] * total
    got_acc = OUT_ADDR
    fr_addr, win_addr = IN_ADDR, IN_ADDR + 0x10000
    mem = None
    # run frame by frame on one persistent memory image (state = acc array)
    from sh4box import BoxMem, ENTRY, MAGIC
    from emu.sh4 import SH4, PcHalt
    mem = BoxMem()
    for i, b in enumerate(code):
        mem.w8(ENTRY + i, b)
    for i, b in enumerate(struct.pack(f"<{n}h", *win)):
        mem.w8(win_addr + i, b)
    for m in range(frames):
        fr = [rnd.randint(-32768, 32767) for _ in range(n)]
        for i, b in enumerate(struct.pack(f"<{n}h", *fr)):
            mem.w8(fr_addr + i, b)
        seg = want[m * hop:m * hop + n]
        M.ola_add_q15(seg, fr, win)
        want[m * hop:m * hop + n] = seg
        c = SH4(mem)
        from emu.sh4 import MD, BL
        c.sr = MD | BL | 0xF0
        c.b0 = [0] * 8
        c.b1 = [0] * 8
        c.rhi = [0] * 8
        c.b0[4], c.b0[5], c.b0[6], c.b0[7] = (got_acc + m * hop * 4, fr_addr,
                                              win_addr, n)
        c.pr, c.pc = MAGIC, ENTRY

        def tr(pc, op):
            if pc == MAGIC:
                raise PcHalt(pc)
        c.trace = tr
        try:
            c.run(limit=2_000_000)
        except PcHalt:
            pass
    got = [struct.unpack("<i", bytes(mem.r8(got_acc + 4 * i + j)
                                     for j in range(4)))[0]
           for i in range(total)]
    bad = sum(1 for a, b in zip(got, want) if a != b)
    print(f"ola n={n} hop={hop} frames={frames} mismatches={bad}")
    return bad == 0


def test_fft(n=256, seed=5):
    import random
    import numpy as np
    rnd = random.Random(seed)
    tw = M.twiddles_q15(n)
    perm = M.bitrev_perm(n)
    x = [int(9000 * math.sin(2 * math.pi * 11.3 * i / n)) +
         int(5000 * math.sin(2 * math.pi * 47 * i / n)) +
         rnd.randint(-2000, 2000) for i in range(n)]
    assert max(abs(v) for v in x) <= 16383
    xs = [[x[perm[i]], 0] for i in range(n)]
    want = M.fft_q15([list(p) for p in xs], tw)
    code = assemble(FFT)
    tw_addr = IN_ADDR + 0x10000
    data = {IN_ADDR: struct.pack(f"<{2 * n}h", *[v for p in xs for v in p]),
            tw_addr: struct.pack(f"<{n}h", *[v for p in tw for v in p])}
    c, mem, res = run_code(code, regs={4: IN_ADDR, 5: tw_addr, 6: n, 7: -1,
                                       15: STACK}, data=data, limit=20_000_000)
    raw = bytes(mem.r8(IN_ADDR + i) for i in range(4 * n))
    got = list(struct.unpack(f"<{2 * n}h", raw))
    flat = [v for p in want for v in p]
    bad = sum(1 for a, b in zip(got, flat) if a != b)
    ref = np.fft.fft(np.array(x, float)) / n
    g = np.array(got[0::2]) + 1j * np.array(got[1::2])
    err = np.abs(g - ref)
    snr = 20 * math.log10(np.abs(ref).max() / err.max())
    print(f"fft n={n} mismatches={bad}  peak-error vs numpy: {err.max():.2f} "
          f"(dynamic range {snr:.1f} dB)  instr={c.steps} "
          f"({c.steps / (n * math.log2(n) / 2):.0f}/butterfly)  ({res})")
    return bad == 0


if __name__ == "__main__":
    ok = test_fft() and test_ola() and all(test_resample(int(round(2 ** (s / 12) * 65536)))
             for s in (-7, -2, 2, 7, 12))
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
