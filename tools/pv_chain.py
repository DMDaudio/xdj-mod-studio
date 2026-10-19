#!/usr/bin/env python3
"""pv_chain.py -- the whole float key-shift chain, every DSP step running
as shasm-assembled SH-4 code on the emulated CPU (fr0-fr11 only, FPSCR
never written). Python only sequences the kernels and moves buffers.

Per 512-sample synthesis hop (stereo, N=2048, two-for-one FFT):
  analysis_pack -> fft -> split -> polar(L,R) -> propagate(L,R) ->
  rect(L,R) -> merge -> ifft -> synth_ola -> emit
then resample_q16 by R = Hs/Ha turns the stretched stream into a pitch
shift (same architecture as tools/pv.py).

  pv_chain.py identity     R=1 pass-through (SNR of output vs input)
  pv_chain.py shift N      +N semitones on a test tone / clip, measured
"""
import math
import struct
import sys
import numpy as np
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import BoxMem, ENTRY, MAGIC
from emu.sh4 import SH4, PcHalt, MD, BL
from emu.sh4fpu import FPU
import pv_float_test as F
import pv_core_test as C

BYPASS = False
LOCK_PEAKS = True
A_NEAR, A_FLAGS = 0x0B4D0000, 0x0B4D4000
N, HS = 2048, 512
NB = N // 2 + 1

ANALYSIS = """
analysis:
  tst r7, r7
  bt done
loop:
  mov.w @r4+, r1
  lds r1, fpul
  float fpul, fr0
  fmov.s @r6+, fr2
  fmul fr2, fr0
  mov.w @r5+, r1
  lds r1, fpul
  float fpul, fr1
  fmul fr2, fr1
  mov.w @r8+, r0
  mov r9, r1
  add r0, r1
  fmov.s fr0, @r1
  add #4, r1
  fmov.s fr1, @r1
  dt r7
  bf loop
done:
  rts
  nop
"""

SPLIT_F = """
split_f:
  mov.l r9, @-r15
  mov.l r10, @-r15
  mov.l r11, @-r15
  mov r7, r11
  add #-1, r11
  mov r7, r9
  shlr r9
  add #1, r9
  mov #0, r10
  fmov.s @r8, fr11
  mov #4, r0
loop:
  mov r7, r1
  sub r10, r1
  and r11, r1
  shll2 r1
  shll r1
  add r4, r1
  mov r10, r2
  shll2 r2
  shll r2
  add r4, r2
  fmov.s @r2, fr0
  fmov.s @(r0,r2), fr1
  fmov.s @r1, fr2
  fmov.s @(r0,r1), fr3
  fmov fr0, fr4
  fadd fr2, fr4
  fmul fr11, fr4
  fmov.s fr4, @r5
  fmov fr1, fr4
  fsub fr3, fr4
  fmul fr11, fr4
  fmov.s fr4, @(r0,r5)
  add #8, r5
  fmov fr1, fr4
  fadd fr3, fr4
  fmul fr11, fr4
  fmov.s fr4, @r6
  fmov fr2, fr4
  fsub fr0, fr4
  fmul fr11, fr4
  fmov.s fr4, @(r0,r6)
  add #8, r6
  add #1, r10
  dt r9
  bf loop
  mov.l @r15+, r11
  mov.l @r15+, r10
  mov.l @r15+, r9
  rts
  nop
"""

# merge into BIT-REVERSED positions: r4=L r5=R (NB bins), r6=Zout, r7=N,
# r8=perm byte-offset table (u16, perm[i]*8)
MERGE_F = """
merge_f:
  mov.l r9, @-r15
  mov.l r10, @-r15
  mov.l r11, @-r15
  mov r7, r11
  add #-1, r11
  mov r7, r9
  shlr r9
  add #1, r9
  mov #0, r10
loop:
  mov #4, r0
  fmov.s @r4, fr0
  fmov.s @(r0,r4), fr1
  fmov.s @r5, fr2
  fmov.s @(r0,r5), fr3
  mov r7, r1
  sub r10, r1
  and r11, r1
  shll r1
  mov r1, r0
  mov.w @(r0,r8), r0
  mov r6, r1
  add r0, r1
  fmov fr0, fr4
  fadd fr3, fr4
  fmov.s fr4, @r1
  add #4, r1
  fmov fr2, fr4
  fsub fr1, fr4
  fmov.s fr4, @r1
  mov r10, r1
  shll r1
  mov r1, r0
  mov.w @(r0,r8), r0
  mov r6, r1
  add r0, r1
  fmov fr0, fr4
  fsub fr3, fr4
  fmov.s fr4, @r1
  add #4, r1
  fmov fr1, fr4
  fadd fr2, fr4
  fmov.s fr4, @r1
  add #8, r4
  add #8, r5
  add #1, r10
  dt r9
  bf loop
  mov.l @r15+, r11
  mov.l @r15+, r10
  mov.l @r15+, r9
  rts
  nop
"""

# generalised phase advance (turns):
#   out = frac(prev + om + r * wrap(ph1 - ph0 - ex)),  wrap(x)=x-floor(x+.5)
# r4=prev r5=ph0 r6=ph1 r7=om r8=out r9=count r10->[0.5, r] r11=ex
PROP2 = """
prop2:
  tst r9, r9
  bt done
  fmov.s @r10+, fr9
  fmov.s @r10, fr10
loop:
  fmov.s @r4+, fr0
  fmov.s @r5+, fr1
  fmov.s @r6+, fr2
  fmov.s @r7+, fr3
  fmov.s @r11+, fr7
  fsub fr1, fr2
  fsub fr7, fr2
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
  fmul fr10, fr2
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

# r4=Z (natural order), r5=accL, r6=accR, r7=N, r8=synth window (float)
SYNTH = """
synth:
  tst r7, r7
  bt done
loop:
  fmov.s @r4+, fr0
  fmov.s @r4+, fr1
  fmov.s @r8+, fr2
  fmul fr2, fr0
  fmul fr2, fr1
  fmov.s @r5, fr3
  fadd fr0, fr3
  fmov.s fr3, @r5
  add #4, r5
  fmov.s @r6, fr3
  fadd fr1, fr3
  fmov.s fr3, @r6
  add #4, r6
  dt r7
  bf loop
done:
  rts
  nop
"""

# r4=acc float, r5=s16 out, r6=count, r8 -> [32767.0, 0.5]
EMIT = """
emit:
  tst r6, r6
  bt done
  fmov.s @r8+, fr8
  fmov.s @r8, fr9
  fmov fr8, fr10
  fneg fr10
loop:
  fmov.s @r4+, fr0
  fmul fr8, fr0
  fldi0 fr1
  fcmp/gt fr0, fr1
  bt neg
  fadd fr9, fr0
  bra clamp
  nop
neg:
  fsub fr9, fr0
clamp:
  fcmp/gt fr8, fr0
  bf c1
  fmov fr8, fr0
c1:
  fcmp/gt fr0, fr10
  bf c2
  fmov fr10, fr0
c2:
  ftrc fr0, fpul
  sts fpul, r1
  mov.w r1, @r5
  add #2, r5
  dt r6
  bf loop
done:
  rts
  nop
"""


class Box:
    """Persistent emulated SH-4 with kernels loaded at fixed addresses."""

    def __init__(self):
        self.mem = BoxMem()
        self.cpu = SH4(self.mem)
        self.cpu.fpu = FPU(self.cpu)
        self.addr = {}
        self.next = 0x0B100000
        self.steps = {}

    def load(self, name, src):
        code = assemble(src)
        self.addr[name] = self.next
        for i, b in enumerate(code):
            self.mem.w8(self.next + i, b)
        self.next += (len(code) + 63) & ~31

    def call(self, name, **regs):
        c = self.cpu
        c.sr = MD | BL | 0xF0
        c.b0, c.b1, c.rhi = [0] * 8, [0] * 8, [0] * 8
        regs.setdefault("r15", 0x0B3F0000)
        for k, v in regs.items():
            r = int(k[1:])
            if r < 8:
                c.b0[r] = v & 0xFFFFFFFF
            else:
                c.rhi[r - 8] = v & 0xFFFFFFFF
        c.pr, c.pc = MAGIC, self.addr[name]
        s0 = c.steps

        def tr(pc, op):
            if pc == MAGIC:
                raise PcHalt(pc)
        c.trace = tr
        try:
            c.run(limit=200_000_000)
        except PcHalt:
            pass
        c.trace = None
        self.steps[name] = self.steps.get(name, 0) + c.steps - s0

    # memory helpers
    def put(self, addr, blob):
        for i, b in enumerate(blob):
            self.mem.w8(addr + i, b)

    def get(self, addr, n):
        return bytes(self.mem.r8(addr + i) for i in range(n))

    def getf(self, addr, n):
        return np.frombuffer(self.get(addr, 4 * n), "<f4").astype(np.float64)


# memory map (all inside the box's 64 MB)
A_LIN, A_RIN = 0x0B400000, 0x0B402000     # s16 frame inputs (N)
A_Z, A_TW, A_TWC = 0x0B420000, 0x0B430000, 0x0B434000
A_L, A_R = 0x0B440000, 0x0B444000         # split outputs (NB complex = 8200 B)
A_MAG, A_PH = 0x0B450000, 0x0B458000      # per channel, 0x4000 apart
A_PH0, A_PREV, A_NEW = 0x0B460000, 0x0B468000, 0x0B470000
A_OM, A_EX, A_CONST = 0x0B478000, 0x0B47A000, 0x0B47C000
A_ACL, A_ACR = 0x0B480000, 0x0B484000     # (N+HS) floats = 10240 B each
A_WIN, A_WSYN, A_PERM = 0x0B488000, 0x0B48C000, 0x0B490000
A_OUTL, A_OUTR = 0x0B4A0000, 0x0B4B0000   # emitted s16
A_LTAB = 0x0B4C0000                       # polar/rect coefficient table
STRIDE = 0x2000


def f32(a):
    return np.asarray(a, np.float32).tobytes()


def build(box):
    for name, src in (("analysis", ANALYSIS), ("fft", F.FFT_F), ("split", SPLIT_F),
                      ("polar", F.POLAR), ("prop", PROP2), ("rect", F.RECT),
                      ("merge", MERGE_F), ("synth", SYNTH), ("emit", EMIT),
                      ("resample", C.RESAMPLE), ("peaks", PEAKS), ("lock", LOCK)):
        box.load(name, src)
    k = np.arange(N // 2)
    tw = np.exp(-2j * np.pi * k / N)
    for base, w in ((A_TW, tw), (A_TWC, tw.conj())):
        t = np.empty(N, np.float32)
        t[0::2], t[1::2] = w.real, w.imag
        box.put(base, t.tobytes())
    perm = np.array([int(format(i, "011b")[::-1], 2) for i in range(N)])
    box.put(A_PERM, (perm * 8).astype("<u2").tobytes())
    hann = np.hanning(N + 1)[:N]
    box.put(A_WIN, f32(hann / 32768.0))                    # includes s16 -> float
    # synthesis window: hann / (sum hann^2 at hop N/4 = 1.5) / N (unscaled ifft)
    box.put(A_WSYN, f32(hann / 1.5 / N))
    box.put(A_LTAB, f32(F.TAB_VALS))
    box.put(A_CONST, f32([0.5, 1.0, 32767.0, 0.5, 0.0]))


def run_chain(x_l, x_r, ratio_num_den=None, ha=HS, verbose=False, bypass=False):
    """x_l/x_r: int16 arrays. Returns (outL, outR, box) with the stretched
    (pre-resample) stream, HS-sized blocks emitted after each frame with
    3 frames of latency."""
    box = Box()
    build(box)
    r_scale = HS / ha
    b = np.arange(NB)
    om = (b * HS / N) % 1.0
    ex = (b * ha / N) % 1.0
    box.put(A_OM, f32(om))
    box.put(A_EX, f32(ex))
    box.put(A_CONST + 0x100, f32([0.5, r_scale]))
    nframes = (len(x_l) - N) // ha + 1
    accL = np.zeros(N + HS, np.float32)
    accR = np.zeros(N + HS, np.float32)
    outs_l, outs_r = [], []
    first = True
    for m in range(nframes):
        s = m * ha
        box.put(A_LIN, x_l[s:s + N].astype("<i2").tobytes())
        box.put(A_RIN, x_r[s:s + N].astype("<i2").tobytes())
        box.call("analysis", r4=A_LIN, r5=A_RIN, r6=A_WIN, r7=N, r8=A_PERM, r9=A_Z)
        box.call("fft", r4=A_Z, r5=A_TW, r6=N)
        box.call("split", r4=A_Z, r5=A_L, r6=A_R, r7=N, r8=A_CONST)
        for ch, src in ((0, A_L), (1, A_R)):
            mag, ph = A_MAG + ch * STRIDE * 2, A_PH + ch * STRIDE * 2
            box.call("polar", r4=src, r5=mag, r6=ph, r7=NB, r8=A_LTAB)
        mag_a = [A_MAG + c * STRIDE * 2 for c in (0, 1)]
        for ch in (0, 1):
            ph = A_PH + ch * STRIDE * 2
            ph0 = A_PH0 + ch * STRIDE * 2
            prev = A_PREV + ch * STRIDE * 2
            new = A_NEW + ch * STRIDE * 2
            if first or bypass:
                box.put(prev, box.get(ph, 4 * NB))
            else:
                box.call("prop", r4=prev, r5=ph0, r6=ph, r7=A_OM, r8=new, r9=NB,
                         r10=A_CONST + 0x100, r11=A_EX)
                if LOCK_PEAKS:
                    box.call("peaks", r4=mag_a[ch], r5=A_NEAR, r6=NB, r7=A_FLAGS)
                    box.call("lock", r4=new, r5=ph, r6=A_NEAR, r7=prev, r8=NB)
                else:
                    box.put(prev, box.get(new, 4 * NB))
            box.put(ph0, box.get(ph, 4 * NB))
            mag = A_MAG + ch * STRIDE * 2
            box.call("rect", r4=mag, r5=prev, r6=(A_L if ch == 0 else A_R), r7=NB,
                     r8=A_LTAB)
        first = False
        box.call("merge", r4=A_L, r5=A_R, r6=A_Z, r7=N, r8=A_PERM)
        box.call("fft", r4=A_Z, r5=A_TWC, r6=N)
        box.put(A_ACL, accL.tobytes())
        box.put(A_ACR, accR.tobytes())
        box.call("synth", r4=A_Z, r5=A_ACL, r6=A_ACR, r7=N, r8=A_WSYN)
        accL = np.frombuffer(box.get(A_ACL, 4 * (N + HS)), "<f4").copy()
        accR = np.frombuffer(box.get(A_ACR, 4 * (N + HS)), "<f4").copy()
        if m >= 3:
            box.put(A_ACL, accL.tobytes())
            box.put(A_ACR, accR.tobytes())
            box.call("emit", r4=A_ACL, r5=A_OUTL, r6=HS, r8=A_CONST + 8)
            box.call("emit", r4=A_ACR, r5=A_OUTR, r6=HS, r8=A_CONST + 8)
            outs_l.append(np.frombuffer(box.get(A_OUTL, 2 * HS), "<i2").copy())
            outs_r.append(np.frombuffer(box.get(A_OUTR, 2 * HS), "<i2").copy())
        accL = np.concatenate([accL[HS:], np.zeros(HS, np.float32)])
        accR = np.concatenate([accR[HS:], np.zeros(HS, np.float32)])
        if verbose and m % 5 == 0:
            print(f"  frame {m}/{nframes}", flush=True)
    return np.concatenate(outs_l), np.concatenate(outs_r), box


# peak finding (pv.py semantics): r4=mag (float), r5=near (u16 out), r6=NB,
# r7=flags scratch (bytes). pk[0]: a0>=a1; pk[NB-1]: a>a[-2]; else a>left and
# a>=right. near[b] = closer of previous/next peak (next only if STRICTLY
# closer; before the first peak -> 0).
PEAKS = """
peaks:
  mov.l r8, @-r15
  mov.l r9, @-r15
  mov.l r10, @-r15
  mov.l r11, @-r15
  mov r6, r8
  add #-1, r8
  mov #0, r10
  mov #0, r11
fwd:
  mov r10, r0
  shll2 r0
  fmov.s @(r0,r4), fr0
  mov #0, r1
  tst r10, r10
  bt noleft
  add #-4, r0
  fmov.s @(r0,r4), fr1
  add #4, r0
  fcmp/gt fr1, fr0
  bf notpk
noleft:
  cmp/eq r10, r8
  bt ispk
  add #4, r0
  fmov.s @(r0,r4), fr2
  fcmp/gt fr0, fr2
  bt notpk
ispk:
  mov #1, r1
  mov r10, r11
notpk:
  mov r10, r0
  shll r0
  mov.w r11, @(r0,r5)
  mov r7, r2
  add r10, r2
  mov.b r1, @r2
  add #1, r10
  cmp/eq r6, r10
  bf fwd
  mov r8, r10
  mov r8, r9
bwd:
  mov r7, r2
  add r10, r2
  mov.b @r2, r1
  tst r1, r1
  bt nopk2
  mov r10, r9
nopk2:
  mov r10, r0
  shll r0
  mov.w @(r0,r5), r3
  mov r10, r1
  sub r3, r1
  mov r9, r2
  sub r10, r2
  cmp/gt r2, r1
  bf keep
  mov.w r9, @(r0,r5)
keep:
  tst r10, r10
  bt fin
  add #-1, r10
  bra bwd
  nop
fin:
  mov.l @r15+, r11
  mov.l @r15+, r10
  mov.l @r15+, r9
  mov.l @r15+, r8
  rts
  nop
"""

# lock: out[b] = frac(new[near[b]] + ph[b] - ph[near[b]])
# r4=new r5=ph r6=near(u16) r7=out r8=count
LOCK = """
lock:
  tst r8, r8
  bt done
  mov.l r10, @-r15
  mov #0, r10
loop:
  mov r10, r0
  shll r0
  mov.w @(r0,r6), r1
  shll2 r1
  mov r1, r0
  fmov.s @(r0,r4), fr0
  fmov.s @(r0,r5), fr1
  mov r10, r0
  shll2 r0
  fmov.s @(r0,r5), fr2
  fsub fr1, fr2
  fadd fr2, fr0
  fmov fr0, fr4
  ftrc fr4, fpul
  float fpul, fr5
  fcmp/gt fr4, fr5
  bf nf
  fldi1 fr6
  fsub fr6, fr5
nf:
  fsub fr5, fr0
  fmov.s fr0, @(r0,r7)
  add #1, r10
  cmp/eq r8, r10
  bf loop
  mov.l @r15+, r10
done:
  rts
  nop
"""


def nearest_ref(mag):
    """pv.py's peak / nearest-peak logic, exactly."""
    nb = len(mag)
    pk = np.zeros(nb, bool)
    pk[1:-1] = (mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:])
    pk[0] = mag[0] >= mag[1]
    pk[-1] = mag[-1] > mag[-2]
    near = np.zeros(nb, int)
    last = 0
    for b in range(nb):
        if pk[b]:
            last = b
        near[b] = last
    nxt = nb - 1
    for b in range(nb - 1, -1, -1):
        if pk[b]:
            nxt = b
        if abs(b - nxt) < abs(b - near[b]):
            near[b] = nxt
    return near


def lock_test(seed=11):
    rng = np.random.default_rng(seed)
    box = Box()
    for name, src in (("peaks", PEAKS), ("lock", LOCK)):
        box.load(name, src)
    ok = True
    for trial in range(4):
        mag = np.abs(rng.standard_normal(NB)) ** 2
        if trial == 1:
            mag[::7] = 0.0                    # plateaus / ties
        if trial == 2:
            mag = np.round(mag)               # many exact ties
        if trial == 3:
            mag[0], mag[-1] = 10, 10
        box.put(0x0B400000, f32(mag))
        box.call("peaks", r4=0x0B400000, r5=0x0B410000, r6=NB, r7=0x0B420000)
        got = np.frombuffer(box.get(0x0B410000, 2 * NB), "<u2").astype(int)
        want = nearest_ref(np.float32(mag).astype(float))
        bad = int((got != want).sum())
        new = rng.uniform(0, 1, NB)
        ph = rng.uniform(-0.5, 0.5, NB)
        box.put(0x0B430000, f32(new))
        box.put(0x0B438000, f32(ph))
        box.call("lock", r4=0x0B430000, r5=0x0B438000, r6=0x0B410000, r7=0x0B440000, r8=NB)
        out = box.getf(0x0B440000, NB)
        n32, p32 = np.float32(new).astype(float), np.float32(ph).astype(float)
        ref = n32[want] + p32 - p32[want]
        ref -= np.floor(ref)
        d = np.abs(((out - ref) + 0.5) % 1.0 - 0.5).max()
        print(f"trial {trial}: near mismatches {bad}  lock err {d:.1e} turns  "
              f"({box.steps['peaks'] // (trial + 1) / NB:.0f}+{box.steps['lock'] // (trial + 1) / NB:.0f} instr/bin)")
        ok &= bad == 0 and d < 1e-5
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


def identity_test(secs=0.45):
    sr = 44100
    n = int(secs * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(4)
    xl = (9000 * np.sin(2 * np.pi * 440 * t) + 4000 * np.sin(2 * np.pi * 1234.5 * t) +
          rng.integers(-800, 800, n)).astype(np.int16)
    xr = (7000 * np.sin(2 * np.pi * 660 * t + 1) + rng.integers(-800, 800, n)).astype(np.int16)
    ol, orr, box = run_chain(xl, xr, verbose=True, bypass=BYPASS)
    # output block j corresponds to input block j (frame j+3 finishes it)
    L = len(ol)
    ref_l, ref_r = xl[3 * HS:3 * HS + L].astype(float), xr[3 * HS:3 * HS + L].astype(float)
    skip = 0
    def snr(o, r):
        e = o[skip:].astype(float) - r[skip:]
        return 20 * math.log10(np.sqrt((r[skip:] ** 2).mean()) / max(np.sqrt((e ** 2).mean()), 1e-9))
    sl, sr_ = snr(ol, ref_l), snr(orr, ref_r)
    print(f"identity: L SNR {sl:.1f} dB, R SNR {sr_:.1f} dB over {L} samples")
    tot = sum(box.steps.values())
    hops = (len(xl) - N) // HS + 1
    print(f"instr per hop {tot / hops / 1e6:.2f} M  -> {tot / hops * 44100 / HS / 1e6:.0f} M instr/s")
    for k, v in sorted(box.steps.items(), key=lambda kv: -kv[1]):
        print(f"   {k:9s} {v / hops / 1e3:8.1f} k/hop")
    return min(sl, sr_) > 60


def shift_chain(xl, xr, semitones):
    """Full key shift: stretch by Hs/Ha through the chain, then resample
    by the same ratio (both on the emulated SH-4)."""
    R = 2.0 ** (semitones / 12.0)
    ha = int(round(HS / R))
    Rq = HS / ha                                  # the ratio actually used
    ol, orr, box = run_chain(xl, xr, ha=ha)
    step = int(round(Rq * 65536))
    outs = []
    for ch in (ol, orr):
        n = len(ch)
        n_out = ((n - 2) << 16) // step
        box.put(0x0B500000, ch.astype("<i2").tobytes() + b"\0\0\0\0")
        box.call("resample", r4=0x0B600000, r5=0x0B500000, r6=n_out, r7=step)
        outs.append(np.frombuffer(box.get(0x0B600000, 2 * n_out), "<i2").copy())
    return outs[0], outs[1], Rq, box


def shift_test(semitones=2, secs=0.9):
    sr = 44100
    n = int(secs * sr)
    t = np.arange(n) / sr
    x = (9000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    ol, orr, Rq, box = shift_chain(x, x, semitones)
    seg = ol[4000:].astype(float)
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 18))
    f = np.fft.rfftfreq(1 << 18, 1 / sr)
    pk = f[sp.argmax()]
    want = 440 * Rq
    near = sp[(f > want * .98) & (f < want * 1.02)].sum() / sp.sum()
    print(f"tone shift {semitones:+d} st: peak {pk:.2f} Hz (want {want:.2f}; exact "
          f"{440 * 2 ** (semitones / 12):.2f})  on-note {near * 100:.1f}%")
    return abs(pk - want) < 1.5 and near > 0.97


def clip_test(semitones=2, secs=2.5, path="work/shift_samples/keyshift_+0.wav"):
    import wave, keydetect as K
    with wave.open(path, "rb") as w:
        ch, sr = w.getnchannels(), w.getframerate()
        raw = w.readframes(int(secs * sr))
    a = np.frombuffer(raw, "<i2").reshape(-1, ch)
    xl, xr = a[:, 0].copy(), a[:, -1].copy()
    ol, orr, Rq, box = shift_chain(xl, xr, semitones)
    t0, m0, c0 = K.detect(xl.astype(float) / 32768, sr)
    t1, m1, c1 = K.detect(ol.astype(float) / 32768, sr)
    print(f"clip: in {K.name(t0, m0)} (conf {c0:.2f}) -> out {K.name(t1, m1)} (conf {c1:.2f}); "
          f"expected tonic {K.NOTES[(t0 + semitones) % 12]}")
    import wave as W
    with W.open("work/shift_samples/chain_%+d.wav" % semitones, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        m = min(len(ol), len(orr))
        w.writeframes(np.column_stack([ol[:m], orr[:m]]).astype("<i2").tobytes())
    tot = sum(box.steps.values())
    print(f"total {tot / 1e6:.0f} M instr for {secs}s audio -> {tot / secs / 1e6:.0f} M instr/s")
    return t1 == (t0 + semitones) % 12


if __name__ == "__main__":
    if sys.argv[1:2] == ["lock"]:
        sys.exit(0 if lock_test() else 1)
    if sys.argv[1:2] == ["shift"]:
        st = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        ok = shift_test(st)
        ok = clip_test(st) and ok
        sys.exit(0 if ok else 1)
    if sys.argv[1:2] == ["identity"]:
        sys.exit(0 if identity_test() else 1)
    print(__doc__)
