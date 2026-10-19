#!/usr/bin/env python3
"""pv_float_test.py -- single-precision FPU kernels for the key shifter,
run on the emulated SH-4 and checked against numpy (float64 reference).
Register discipline: fr0..fr9 only (the firmware's interrupt/task entry
saves fr0-fr11, FPUL, FPSCR -- see BEATJUMP.md); FPSCR is never written."""
import math
import struct
import sys
import numpy as np
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code

FFT_F = """
fft_f:
  mov.l r8, @-r15
  mov.l r9, @-r15
  mov.l r10, @-r15
  add #-16, r15
  mov.l r4, @r15
  mov.l r5, @(4,r15)
  mov r6, r7
  shll2 r7
  shll r7
  mov r4, r1
  add r7, r1
  mov.l r1, @(8,r15)
  mov r7, r10
  shlr r10
  mov.l r10, @(12,r15)
  mov #8, r9
stage:
  mov.l @r15, r1
  mov r1, r2
  add r9, r2
block:
  mov.l @(4,r15), r3
  mov r9, r8
  shlr2 r8
  shlr r8
inner:
  mov #4, r0
  fmov.s @r2, fr0
  fmov.s @(r0,r2), fr1
  fmov.s @r3, fr2
  fmov.s @(r0,r3), fr3
  fmov fr0, fr4
  fmul fr2, fr4
  fmov fr1, fr5
  fmul fr3, fr5
  fsub fr5, fr4
  fmul fr3, fr0
  fmul fr2, fr1
  fadd fr1, fr0
  fmov.s @r1, fr6
  fmov.s @(r0,r1), fr7
  fmov fr6, fr8
  fmov fr7, fr9
  fadd fr4, fr6
  fadd fr0, fr7
  fsub fr4, fr8
  fsub fr0, fr9
  fmov.s fr6, @r1
  fmov.s fr7, @(r0,r1)
  fmov.s fr8, @r2
  fmov.s fr9, @(r0,r2)
  add #8, r1
  add #8, r2
  add r10, r3
  dt r8
  bf inner
  mov r2, r1
  add r9, r2
  mov.l @(8,r15), r11
  cmp/hs r11, r1
  bf block
  mov.l @(12,r15), r11
  cmp/eq r11, r9
  bt done
  shll r9
  shlr r10
  bra stage
  nop
done:
  add #16, r15
  mov.l @r15+, r10
  mov.l @r15+, r9
  mov.l @r15+, r8
  rts
  nop
"""

X, TW, PERM, STK = 0x0B200000, 0x0B280000, 0x0B2C0000, 0x0B3F0000


def f32(a):
    return struct.pack(f"<{len(a)}f", *a)


def rdf(mem, addr, n):
    return list(struct.unpack(f"<{n}f", bytes(mem.r8(addr + i) for i in range(4 * n))))


def bitrev(n):
    b = n.bit_length() - 1
    return [int(format(i, f"0{b}b")[::-1], 2) for i in range(n)]


def test_fft(n=2048, seed=1):
    rng = np.random.default_rng(seed)
    x = (0.4 * np.sin(2 * np.pi * 37.3 * np.arange(n) / n) +
         0.1 * rng.standard_normal(n)) + 1j * (0.3 * np.sin(2 * np.pi * 91 * np.arange(n) / n))
    perm = bitrev(n)
    xb = x[perm]
    inter = np.empty(2 * n, np.float32)
    inter[0::2], inter[1::2] = xb.real, xb.imag
    k = np.arange(n // 2)
    tw = np.exp(-2j * np.pi * k / n)
    twi = np.empty(n, np.float32)
    twi[0::2], twi[1::2] = tw.real, tw.imag
    c, mem, res = run_code(assemble(FFT_F),
                           regs={4: X, 5: TW, 6: n, 15: STK},
                           data={X: f32(inter), TW: f32(twi)}, limit=50_000_000)
    got = np.array(rdf(mem, X, 2 * n))
    g = got[0::2] + 1j * got[1::2]
    ref = np.fft.fft(x)
    err = np.abs(g - ref).max()
    snr = 20 * math.log10(np.abs(ref).max() / err)
    bfly = n * math.log2(n) / 2
    print(f"float fft n={n}: max err {err:.2e}  dynamic range {snr:.0f} dB  "
          f"instr={c.steps} ({c.steps / bfly:.1f}/butterfly)  ({res})")
    return snr > 110


# atan(a) ~ a*(c0 + c1 a^2 + ... + c4 a^8), |err| < 1e-5 rad; coefficients
# pre-divided by 2*pi so results are in TURNS (1.0 = full circle).
ATAN_C = [0.0208351, -0.0851330, 0.1801410, -0.3302995, 0.9998660]
TAB_VALS = [c / (2 * math.pi) for c in ATAN_C] + [0.25, 0.5, 65536.0]

# polar: r4=spec (complex f32, 8 B/bin), r5=mag out, r6=phase out (turns),
# r7=count, r8=constant table. Uses fr0-fr11 only.
POLAR = """
polar:
  tst r7, r7
  bt done
loop:
  fmov.s @r4+, fr10
  fmov.s @r4+, fr11
  fmov fr10, fr8
  fmul fr10, fr8
  fmov fr11, fr9
  fmul fr11, fr9
  fadd fr9, fr8
  fsqrt fr8
  fmov.s fr8, @r5
  add #4, r5
  fmov fr10, fr2
  fabs fr2
  fmov fr11, fr3
  fabs fr3
  fldi0 fr6
  fmov fr3, fr4
  fmov fr2, fr5
  mov #0, r1
  fcmp/gt fr2, fr3
  bf noswap
  fmov fr2, fr4
  fmov fr3, fr5
  mov #1, r1
noswap:
  fcmp/eq fr6, fr5
  bt zero
  fdiv fr5, fr4
  fmov fr4, fr1
  fmul fr4, fr4
  fmov fr4, fr0
  mov r8, r3
  fmov.s @r3+, fr2
  fmov.s @r3+, fr3
  fmac fr0, fr2, fr3
  fmov.s @r3+, fr2
  fmac fr0, fr3, fr2
  fmov.s @r3+, fr3
  fmac fr0, fr2, fr3
  fmov.s @r3+, fr2
  fmac fr0, fr3, fr2
  fmul fr1, fr2
  mov #20, r0
  tst r1, r1
  bt noq
  fmov.s @(r0,r8), fr3
  fsub fr2, fr3
  fmov fr3, fr2
noq:
  fldi0 fr7
  fcmp/gt fr10, fr7
  bf posx
  mov #24, r0
  fmov.s @(r0,r8), fr3
  fsub fr2, fr3
  fmov fr3, fr2
posx:
  fcmp/gt fr7, fr11
  bt out
  fneg fr2
  bra out
  nop
zero:
  fldi0 fr2
out:
  fmov.s fr2, @r6
  add #4, r6
  dt r7
  bf loop
done:
  rts
  nop
"""

# rect: r4=mag in, r5=phase in (turns), r6=complex out, r7=count, r8=table.
RECT = """
rect:
  tst r7, r7
  bt done
  mov #28, r0
  fmov.s @(r0,r8), fr4
  mov #4, r0
loop:
  fmov.s @r4+, fr1
  fmov.s @r5+, fr5
  fmul fr4, fr5
  ftrc fr5, fpul
  fsca fpul, dr2
  fmul fr1, fr3
  fmul fr1, fr2
  fmov.s fr3, @r6
  fmov.s fr2, @(r0,r6)
  add #8, r6
  dt r7
  bf loop
done:
  rts
  nop
"""

MAG, PH, OUT, TAB = 0x0B2E0000, 0x0B2F0000, 0x0B300000, 0x0B310000


def test_polar_rect(n=4096, seed=3):
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n) * 10 ** rng.uniform(-3, 1, n) + \
        1j * rng.standard_normal(n) * 10 ** rng.uniform(-3, 1, n)
    z[0] = 0
    z[1] = 1e-3 + 0j
    z[2] = -1 + 0j
    z[3] = 1j
    z[4] = -1j
    inter = np.empty(2 * n, np.float32)
    inter[0::2], inter[1::2] = z.real, z.imag
    data = {X: f32(inter), TAB: f32(TAB_VALS)}
    c, mem, res = run_code(assemble(POLAR), regs={4: X, 5: MAG, 6: PH, 7: n, 8: TAB,
                                                  15: STK}, data=data, limit=50_000_000)
    mag = np.array(rdf(mem, MAG, n))
    ph = np.array(rdf(mem, PH, n))
    zi = z.astype(np.complex64).astype(complex)
    emag = np.abs(mag - np.abs(zi)).max() / np.abs(zi).max()
    dph = ((ph - np.angle(zi) / (2 * np.pi)) + 0.5) % 1.0 - 0.5
    # exclude the exact-zero bin (defined as phase 0)
    eph = np.abs(dph[1:]).max()
    print(f"polar n={n}: mag rel err {emag:.1e}  phase err {eph:.1e} turns "
          f"({eph * 2 * np.pi:.1e} rad)  {c.steps / n:.0f} instr/bin  ({res})")
    ok = emag < 1e-6 and eph * 2 * np.pi < 2e-5
    # rect from the kernel's own outputs (round trip)
    data2 = {MAG: f32(list(mag)), PH: f32(list(ph))}
    c, mem, res = run_code(assemble(RECT), regs={4: MAG, 5: PH, 6: OUT, 7: n, 8: TAB,
                                                 15: STK}, data={**data2, TAB: f32(TAB_VALS)},
                           limit=50_000_000)
    o = np.array(rdf(mem, OUT, 2 * n))
    back = o[0::2] + 1j * o[1::2]
    erel = np.abs(back - zi).max() / np.abs(zi).max()
    print(f"rect  n={n}: round-trip err {erel:.1e} of peak  {c.steps / n:.0f} instr/bin  ({res})")
    ok &= erel < 3e-4        # phase quantised to 1/65536 turn by FSCA
    return ok


# propagate (phase-vocoder advance, all phases in TURNS):
#   out = frac(prev + om + wrap(ph1 - ph0 - om)),  wrap(x) = x - floor(x+.5)
# r4=prev r5=ph0 r6=ph1 r7=omega (float arrays), r8=out, r9=count,
# r10 -> [0.5]
PROP = """
prop:
  tst r9, r9
  bt done
  fmov.s @r10, fr9
loop:
  fmov.s @r4+, fr0
  fmov.s @r5+, fr1
  fmov.s @r6+, fr2
  fmov.s @r7+, fr3
  fsub fr1, fr2
  fsub fr3, fr2
  fmov fr2, fr4
  fadd fr9, fr4
  ftrc fr4, fpul
  float fpul, fr5
  fcmp/gt fr4, fr5
  bf nofix1
  fldi1 fr6
  fsub fr6, fr5
nofix1:
  fsub fr5, fr2
  fadd fr3, fr2
  fadd fr0, fr2
  fmov fr2, fr4
  ftrc fr4, fpul
  float fpul, fr5
  fcmp/gt fr4, fr5
  bf nofix2
  fldi1 fr6
  fsub fr6, fr5
nofix2:
  fsub fr5, fr2
  fmov.s fr2, @r8
  add #4, r8
  dt r9
  bf loop
done:
  rts
  nop
"""


def test_prop(n=1025, seed=9):
    rng = np.random.default_rng(seed)
    prev = rng.uniform(0, 1, n)
    ph0 = rng.uniform(-0.5, 0.5, n)
    ph1 = rng.uniform(-0.5, 0.5, n)
    om = (np.arange(n) * 0.25) % 1.0
    A, B, C, D, E = 0x0B320000, 0x0B322000, 0x0B324000, 0x0B326000, 0x0B328000
    data = {A: f32(prev), B: f32(ph0), C: f32(ph1), D: f32(om), E: f32([0.5])}
    c, mem, res = run_code(assemble(PROP), regs={4: A, 5: B, 6: C, 7: D, 8: 0x0B32A000,
                                                 9: n, 10: E, 15: STK}, data=data,
                           limit=10_000_000)
    got = np.array(rdf(mem, 0x0B32A000, n))
    p, a, b, o = (np.float32(v).astype(float) for v in (prev, ph0, ph1, om))
    x = b - a - o
    ref = p + o + (x - np.floor(x + 0.5))
    ref = ref - np.floor(ref)
    d = np.abs(((got - ref) + 0.5) % 1.0 - 0.5).max()
    print(f"propagate n={n}: max err {d:.1e} turns  {c.steps / n:.0f} instr/bin  ({res})")
    return d < 1e-5


if __name__ == "__main__":
    ok = test_fft(256) and test_fft(2048) and test_polar_rect() and test_prop()
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
