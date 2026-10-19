#!/usr/bin/env python3
"""pv_stereo_test.py -- bit-check bit-reverse / split / merge kernels and
the full two-for-one stereo round trip (L+iR -> FFT -> split -> merge ->
IFFT) against the integer model and numpy."""
import math
import struct
import sys
import numpy as np
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code
import pv_fixed as M
import pv_core_test as C

BITREV = """
bitrev:
  tst r7, r7
  bt done
loop:
  mov.w @r6+, r0
  shll2 r0
  mov.l @(r0,r4), r1
  mov.l r1, @r5
  add #4, r5
  dt r7
  bf loop
done:
  rts
  nop
"""

SPLIT = """
split:
  mov.l r8, @-r15
  mov.l r9, @-r15
  mov.l r10, @-r15
  mov.l r11, @-r15
  mov.l r12, @-r15
  mov.l r13, @-r15
  mov.l r14, @-r15
  mov r7, r8
  add #-1, r8
  mov r7, r9
  shlr r9
  add #1, r9
  mov #0, r10
loop:
  mov r7, r1
  sub r10, r1
  and r8, r1
  shll2 r1
  add r4, r1
  mov r10, r2
  shll2 r2
  add r4, r2
  mov #2, r0
  mov.w @r2, r11
  mov.w @(r0,r2), r12
  mov.w @r1, r13
  mov.w @(r0,r1), r14
  mov r11, r3
  add r13, r3
  shar r3
  mov.w r3, @r5
  mov r12, r3
  sub r14, r3
  shar r3
  mov.w r3, @(r0,r5)
  add #4, r5
  mov r12, r3
  add r14, r3
  shar r3
  mov.w r3, @r6
  mov r13, r3
  sub r11, r3
  shar r3
  mov.w r3, @(r0,r6)
  add #4, r6
  add #1, r10
  dt r9
  bf loop
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

MERGE = """
merge:
  mov.l r8, @-r15
  mov.l r9, @-r15
  mov.l r10, @-r15
  mov.l r11, @-r15
  mov.l r12, @-r15
  mov.l r13, @-r15
  mov.l r14, @-r15
  mov r7, r8
  add #-1, r8
  mov r7, r9
  shlr r9
  add #1, r9
  mov #0, r10
  mov #2, r0
loop:
  mov.w @r4, r11
  mov.w @(r0,r4), r12
  mov.w @r5, r13
  mov.w @(r0,r5), r14
  mov r7, r1
  sub r10, r1
  and r8, r1
  shll2 r1
  add r6, r1
  mov r11, r2
  add r14, r2
  mov.w r2, @r1
  mov r13, r2
  sub r12, r2
  mov.w r2, @(r0,r1)
  mov r10, r1
  shll2 r1
  add r6, r1
  mov r11, r2
  sub r14, r2
  mov.w r2, @r1
  mov r12, r2
  add r13, r2
  mov.w r2, @(r0,r1)
  add #4, r4
  add #4, r5
  add #1, r10
  dt r9
  bf loop
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

A, B, D, E, TW, TWC, PERM, STK = (0x0B200000, 0x0B210000, 0x0B220000,
                                 0x0B230000, 0x0B240000, 0x0B250000,
                                 0x0B260000, 0x0B3F0000)


def s16s(v):
    return struct.pack(f"<{len(v)}h", *v)


def rd(mem, addr, n, fmt="h"):
    sz = struct.calcsize(fmt)
    raw = bytes(mem.r8(addr + i) for i in range(n * sz))
    return list(struct.unpack(f"<{n}{fmt}", raw))


def run(src, regs, data):
    regs = dict(regs)
    regs[15] = STK
    return run_code(assemble(src), regs=regs, data=data, limit=50_000_000)


def main(n=256, seed=7):
    import random
    rnd = random.Random(seed)
    tw = M.twiddles_q15(n)
    twc = [(a, -b) for a, b in tw]
    perm = M.bitrev_perm(n)
    ok = True
    Lx = [int(6000 * math.sin(2 * math.pi * 9.3 * i / n)) + rnd.randint(-1500, 1500) for i in range(n)]
    Rx = [int(6000 * math.sin(2 * math.pi * 31.1 * i / n + 1)) + rnd.randint(-1500, 1500) for i in range(n)]
    z = [[Lx[i], Rx[i]] for i in range(n)]
    flat = [v for p in z for v in p]

    # 1. bitrev kernel
    c, mem, _ = run(BITREV, {4: A, 5: B, 6: PERM, 7: n},
                    {A: s16s(flat), PERM: struct.pack(f"<{n}H", *perm)})
    got = rd(mem, B, 2 * n)
    want = [v for i in range(n) for v in z[perm[i]]]
    bad = sum(a != b for a, b in zip(got, want))
    print(f"bitrev: mismatches={bad} instr={c.steps}"); ok &= bad == 0

    # 2. forward FFT (asm) then split (asm) vs model
    zb = [z[perm[i]] for i in range(n)]
    Zm = M.fft_q15([list(p) for p in zb], tw, 1)
    c, mem, _ = run(C.FFT, {4: A, 5: TW, 6: n, 7: -1},
                    {A: s16s([v for p in zb for v in p]),
                     TW: s16s([v for p in tw for v in p])})
    Zg = rd(mem, A, 2 * n)
    bad = sum(a != b for a, b in zip(Zg, [v for p in Zm for v in p]))
    print(f"fft(stereo packed): mismatches={bad} instr={c.steps}"); ok &= bad == 0
    Lm, Rm = M.split_q15(Zm)
    c, mem, _ = run(SPLIT, {4: A, 5: D, 6: E, 7: n}, {A: s16s(Zg)})
    Lg, Rg = rd(mem, D, n + 2), rd(mem, E, n + 2)
    bad = sum(a != b for a, b in zip(Lg, [v for p in Lm for v in p])) + \
        sum(a != b for a, b in zip(Rg, [v for p in Rm for v in p]))
    print(f"split: mismatches={bad} instr={c.steps} ({c.steps / (n // 2 + 1):.0f}/bin)"); ok &= bad == 0
    refL = np.fft.fft(np.array(Lx, float))[:n // 2 + 1] / n
    refR = np.fft.fft(np.array(Rx, float))[:n // 2 + 1] / n
    gL = np.array(Lg[0::2]) + 1j * np.array(Lg[1::2])
    gR = np.array(Rg[0::2]) + 1j * np.array(Rg[1::2])
    print(f"  L err {np.abs(gL - refL).max():.2f}  R err {np.abs(gR - refR).max():.2f} "
          f"(spectrum peak L {np.abs(refL).max():.0f} R {np.abs(refR).max():.0f})")

    # 3. merge (asm) vs model, then IFFT unscaled -> original L/R
    c, mem, _ = run(MERGE, {4: D, 5: E, 6: A, 7: n}, {D: s16s(Lg), E: s16s(Rg)})
    Zmg = rd(mem, A, 2 * n)
    Zmm = M.merge_q15(Lm, Rm, n)
    bad = sum(a != b for a, b in zip(Zmg, [v for p in Zmm for v in p]))
    print(f"merge: mismatches={bad} instr={c.steps} ({c.steps / (n // 2 + 1):.0f}/bin)"); ok &= bad == 0
    zi = [Zmm[perm[i]] for i in range(n)]
    back = M.fft_q15([list(p) for p in zi], twc, 0)
    errL = max(abs(back[i][0] - Lx[i]) for i in range(n))
    errR = max(abs(back[i][1] - Rx[i]) for i in range(n))
    print(f"round trip (fft/N -> split -> merge -> ifft): max error L {errL} R {errR} LSB "
          f"(signal peak ~7500)")
    rms = math.sqrt(sum((back[i][0] - Lx[i]) ** 2 + (back[i][1] - Rx[i]) ** 2
                        for i in range(n)) / (2 * n))
    sig = math.sqrt(sum(v * v for v in Lx + Rx) / (2 * n))
    print(f"  round-trip SNR {20 * math.log10(sig / rms):.1f} dB  <- KNOWN LIMIT of the "
          f"s16 data path (informational, not asserted; see BEATJUMP.md)")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
