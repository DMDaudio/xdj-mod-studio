#!/usr/bin/env python3
"""pv_run.py -- the fixed-point key-shift chain, end to end on the emulated
SH-4: every DSP step is shasm code, Python only sequences kernels and moves
buffers (same discipline as the float chain in pv_chain.py).

Mono, N-point FFT, analysis hop Ha, synthesis hop Hs (Hs/Ha = the stretch
factor; Ha = round(Hs/R) so any R is reachable within ~half a sample of hop):

  window -> fft -> mag/atan2 -> peaks/nearest -> propagate(+peak lock) ->
  sincos -> rect -> mirror -> conj -> ifft -> window -> ola32 -> emit32

then tools/resample.py's kernel resamples the stretched stream by R, which
turns a stretch into a pitch shift at constant length (pv.py's scheme).

The three glue kernels here are new: MIRR (Hermitian mirror for a real
spectrum), OLA32 (int32 accumulator add) and EMIT32 (Q15 normalise + clamp).

  pv_run.py identity   R=1 pass-through, SNR of output vs input
  pv_run.py shift N    +N semitones, pitch measured with numpy
"""
import math
import struct
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import numpy as np
from shasm import assemble
from sh4box import BoxMem, MAGIC
from emu.sh4 import SH4, PcHalt, MD, BL
import pv_phase as P
from resample import SRC as RESAMPLE_SRC
from fft_fixed import twiddles_q15

N = 1024
HS = 256                      # synthesis hop (N/4)
NB = N // 2 + 1               # bins of a real spectrum

# ---------------------------------------------------------------- glue kernels
# Hermitian mirror: out[N-k] = conj(in[k]) for k = 1..N/2-1; bins 0 and N/2
# keep their real part and get imag = 0.
#   in: r4=RE r5=IM r6=RE_OUT r7=IM_OUT r8=N
MIRR = """
mirr:
  tst r8, r8
  bt mrd
  mov r8, r1
  add #-1, r1
  mov #1, r2
  mov r8, r3
  shlr r3
  add #-1, r3
mr_loop:
  cmp/hs r2, r3
  bf mrd
  mov r2, r14
  shll r14
  mov r1, r9
  shll r9
  mov r4, r10
  add r14, r10
  mov r5, r11
  add r14, r11
  mov.w @r10, r0
  mov r6, r12
  add r14, r12
  mov.w r0, @r12
  mov r6, r12
  add r9, r12
  mov.w r0, @r12
  mov.w @r11, r0
  mov r7, r12
  add r14, r12
  mov.w r0, @r12
  neg r0, r0
  mov r7, r12
  add r9, r12
  mov.w r0, @r12
  add #1, r2
  add #-1, r1
  bra mr_loop
  nop
mrd:
  mov.w @r4, r0
  mov.w r0, @r6
  mov #0, r0
  mov.w r0, @r7
  mov r8, r2
  shlr r2
  shll r2
  add r2, r4
  add r2, r6
  add r2, r7
  mov.w @r4, r0
  mov.w r0, @r6
  mov #0, r0
  mov.w r0, @r7
  rts
  nop
"""

# acc[i] += frame[i]  (int32 accumulator, s16 frame)
#   in: r4=ACC r5=FRAME r6=N
OLA32 = """
ola32:
  tst r6, r6
  bt o32d
  mov r6, r2
o32l:
  mov.l @r4, r1
  mov.w @r5, r0
  exts.w r0, r0
  add r0, r1
  mov.l r1, @r4
  add #4, r4
  add #2, r5
  dt r2
  bf o32l
o32d:
  rts
  nop
"""

# Synthesis FFT: same DIT butterfly as P.FFTSTAGES, but the right shifts stop
# K stages early, so the transform comes out 2**K times larger than a fully
# scaled FFT.
#
# K MUST stay small: an unshifted DIT stage can double a value, and the
# intermediates are int16.  A tone of amplitude A lands in spectrum bins at
# ~A/4, so K unshifted stages peak near A*2^(K-2) -- K=5 wrapped int16 for
# anything louder than A~4000 (that was the ~24 dB frame SNR / 30 dB identity
# ceiling).  K=1 peaks at ~A/2: safe to full scale.  The frame then comes out
# at x*win/2, which EMIT32 restores with its pre-shift.
#
# The stage body is lifted verbatim out of P.FFTSTAGES (asserted below) so the
# butterfly stays bit-identical to the analysis transform; the unshifted copy
# is that body with its four `shar` lines removed.
#   in: r4=RE r5=IM r6=TWB r7=N r8=STAGES r9=K


def _fft_synth():
    src = P.FFTSTAGES
    lines = src.splitlines()
    i = lines.index("fstage:")
    j = lines.index("  shll r9", i)
    body = lines[i + 1:j]                 # per-stage work, ends at `bt fgloop`
    assert body[0].strip() == "mov #0, r12" and body[-1].strip() == "bt fgloop", \
        body[:2] + body[-2:]
    assert sum(1 for l in body if l.strip().startswith("shar r")) == 4, body
    plain = [l for l in body if not l.strip().startswith("shar r")]
    assert len(plain) == len(body) - 4

    def rename(lines_, tag):
        import re as _re
        pat = _re.compile(r"\b(fgloop|fkloop|fkdone)\b")
        return ["  " + pat.sub(lambda m: m.group(1) + tag, l.strip())
                for l in lines_]

    def stage(lines_, label, tag):
        return ([f"{label}{tag}:"] + rename(lines_, tag) +
                ["  shll r9", "  mov.l @r15+, r0", "  shlr r0",
                 "  mov.l r0, @-r15"])

    k = FFTSYN_K
    # No zero-stage guard: STAGES > K is asserted by set_k()/run_chain(),
    # and the old `bf fse / bra fsdone` entry put a branch in bf's delay
    # slot -- a slot-illegal that faulted the kernel on EVERY call (that
    # was the dead imaginary half and the 15-24 dB real part).
    src_lines = [
        "fftsyn:",
        "  mov r8, r1",
        f"  add #{-k}, r1",        # shifted stages = STAGES - K (K < STAGES)
        "  mov #2, r9",
        "  mov #-15, r14",
        "  mov r7, r0",
        "  shlr r0",
        "  mov.l r0, @-r15",
        "  mov r1, r8",
    ]
    src_lines += stage(body, "fsts", "s")
    # TST sets T when r8 == 0, so the LOOP back-edge is BF (branch while the
    # counter is still non-zero).  `bt` here runs STAGES-K+1 shifted stages.
    src_lines += ["  add #-1, r8", "  tst r8, r8", "  bf fstss"]
    for i in range(k):
        src_lines += stage(plain, "fstu", str(i))
    src_lines += ["  add #4, r15", "fsdone:", "  rts", "  nop"]
    return "\n".join(src_lines) + "\n"


FFTSYN_K = 8
FFTSYN = _fft_synth()
# out[i] = clamp((acc[i] * SCALE) >> 16 << FFTSYN_K, -32768, 32767)
#   in: r3=SCALE(Q16) r4=ACC r5=OUT r6=N
#
# SCALE = 2/3 in Q16 (43690) undoes the 1.5 overlap gain of a Hann-windowed
# hop-N/4 OLA.  The 2**FFTSYN_K that folds the synthesis FFT's unscaled
# stages back in is applied as a plain SHIFT here, not folded into SCALE:
# keeping it out of the Q16 multiply is what keeps the 64-bit product's
# 32-bit headroom usable.  With SCALE = 43690 the kernel is exact for
# |acc| < 2**31/43690 = 49151, and the real chain peaks near 1500 (~30x
# margin); folding 2**K into SCALE instead would overflow at full scale.
#
# The product is exact 64-bit (dmuls.l) and >>16 is built from shlr16 /
# shll16 / or (no 32-bit overflow, no dependence on SHAD semantics).
# Rounding is floor.  The FFTSYN_K left shifts come after the >>16 so they
# cannot lose bits, and the clamp runs last, so saturation is still correct.
def _shl(reg, n):
    """SH-4 has SHLL/SHLL2/SHLL8/SHLL16 only (no SHLL4), so 4 = 2+2."""
    out = []
    for bit in (16, 8, 2, 1):
        while n >= bit:
            out.append(f"  shll{bit if bit > 1 else ''} {reg}")
            n -= bit
    assert n == 0
    return out


def _shr(reg, n):
    out = []
    for bit in (16, 8, 2, 1):
        while n >= bit:
            out.append(f"  shlr{bit if bit > 1 else ''} {reg}")
            n -= bit
    assert n == 0
    return out


# SCALE is 2/3 in Q13.  The kernel shifts the accumulator left by
# FFTSYN_K BEFORE the multiply (so no output bit is thrown away) and then
# shifts the 64-bit product right by 13.  The earlier form -- multiply at
# Q16, >>16, then <<FFTSYN_K -- made every output sample a multiple of
# 2**FFTSYN_K, i.e. a 32-LSB quantisation grid on the final audio: that,
# not the phase engine, was the ~38 dB ceiling on the whole chain.
EMIT_Q = 13
EMIT_SCALE = round(2 / 3 * (1 << EMIT_Q))          # 5461
EMIT_K0 = 5                                           # K that EMIT_SCALE was tuned at


def _emit32():
    up = _shl("r1", FFTSYN_K)
    hi = _shl("r12", 32 - EMIT_Q)
    lo = _shr("r0", EMIT_Q)
    return f"""
emit32:
  tst r6, r6
  bt e32d
  mov r6, r2
  mov #64, r10
  shll8 r10
  shll r10
  add #-1, r10
  mov #64, r11
  shll8 r11
  shll r11
  neg r11, r11
e32l:
  mov.l @r4, r1
{chr(10).join(up)}
  dmuls.l r3, r1
  sts mach, r12
  sts macl, r0
{chr(10).join(hi)}
{chr(10).join(lo)}
  add r0, r12
  mov r12, r0
  cmp/gt r10, r0
  bf e32a
  mov r10, r0
e32a:
  cmp/ge r11, r0
  bt e32b
  mov r11, r0
e32b:
  mov.w r0, @r5
  add #4, r4
  add #2, r5
  dt r2
  bf e32l
e32d:
  rts
  nop
"""


EMIT32 = _emit32()

# ---------------------------------------------------------------- memory map
A_XIN, A_FRM, A_WIN, A_WSYN = 0x0B000000, 0x0B040000, 0x0B048000, 0x0B050000
A_ZR, A_ZI = 0x0B060000, 0x0B068000
A_TW = 0x0B070000
A_RE, A_IM = 0x0B080000, 0x0B088000
A_MAG0, A_PH0 = 0x0B090000, 0x0B098000
A_MAG1, A_PH1 = 0x0B0A0000, 0x0B0A8000
# PROP4 reads its +-8 bin contrast probes through this pointer, which sits 8
# bins (16 bytes) below the magnitude block: the probes then need no edge
# clamping, and the zero padding makes a bin at the spectrum edge pass the
# contrast gate.  The padding is never written (the MAG kernel only touches
# A_MAG1..A_MAG1+2nb), so zero_once() at setup is enough.
A_MAGPAD = A_MAG1 - 16
A_PK, A_NEAR = 0x0B0B0000, 0x0B0B4000
A_PREV, A_NEW = 0x0B0B8000, 0x0B0C0000
A_OMA, A_OMS = 0x0B0C4000, 0x0B0C8000
A_SIN, A_COS = 0x0B0D0000, 0x0B0D4000
A_STAB, A_ATAB = 0x0B0D8000, 0x0B0DC000
A_ACC, A_OUT = 0x0B0E0000, 0x0B100000
A_RSRC, A_RDST = 0x0B200000, 0x0B300000
# PROP5 reads last frame's propagated phases to get a STABLE bin-to-peak
# offset; A_PREV is about to be overwritten by the lock, so the snapshot has to
# be taken first.  A_RSRC is idle during the frame loop (only the resampler
# uses it, after the loop).
A_PREVX = A_RSRC

CODE_BASE = 0x0B600000
LIMIT = 400_000_000


class Box:
    """Persistent emulated SH-4 with the kernels resident at fixed addrs."""

    def __init__(self, n=N, hs=HS):
        self.n, self.hs = n, hs
        self.nb = n // 2 + 1
        self.mem = BoxMem()
        self.cpu = SH4(self.mem)
        self.addr = {}
        nxt = CODE_BASE
        for name, src in KERNELS:
            code = assemble(src)
            self.addr[name] = nxt
            for i, b in enumerate(code):
                self.mem.w8(nxt + i, b)
            nxt += (len(code) + 31) & ~31
        self.steps = {}

    def call(self, name, **regs):
        c = self.cpu
        c.sr = MD | BL | 0xF0
        c.b0, c.b1, c.rhi = [0] * 8, [0] * 8, [0] * 8
        c.rhi[7] = 0x0B500000
        for k, v in regs.items():
            i = int(k[1:])
            if i < 8:
                c.b0[i] = v & 0xFFFFFFFF
            else:
                c.rhi[i - 8] = v & 0xFFFFFFFF
        c.pr, c.pc = MAGIC, self.addr[name]
        s0 = c.steps
        bad = []

        def tr(pc, op):
            if pc == MAGIC:
                raise PcHalt(pc)
            if 0x08000000 <= pc < 0x0B000000:
                bad.append((pc, op))
                raise PcHalt(pc)
        c.trace = tr
        try:
            c.run(limit=LIMIT)
        except PcHalt:
            pass
        except IndexError as e:
            raise RuntimeError(f"{name}: bad access {e}; last stray pc/op "
                               f"{bad[-1] if bad else None}") from None
        c.trace = None
        self.steps[name] = self.steps.get(name, 0) + c.steps - s0

    def put(self, addr, blob):
        for i, b in enumerate(blob):
            self.mem.w8(addr + i, b)

    def s16(self, addr, k):
        return np.frombuffer(self.get(addr, 2 * k), "<i2").astype(np.int64)

    def u16(self, addr, k):
        return np.frombuffer(self.get(addr, 2 * k), "<u2").astype(np.int64)

    def get(self, addr, n):
        return bytes(self.mem.r8(addr + i) for i in range(n))


KERNELS = (("bitrev", P.BITREV), ("fftstages", P.FFTSTAGES),
           ("fftsyn", FFTSYN), ("hann", P.HANN),
           ("mag", P.MAG), ("atan2", P.ATAN2), ("peaks", P.PEAKS),
           ("nearest", P.NEAREST), ("prop", P.PROP), ("prop2", P.PROP2),
           ("prop3", P.PROP3), ("prop4", P.PROP4), ("prop5", P.PROP5),
           ("sins", P.SINS), ("coss", P.COSS), ("rect", P.RECT),
           ("conj", P.CONJ), ("mirr", MIRR), ("ola32", OLA32),
           ("emit32", EMIT32), ("resample", RESAMPLE_SRC))


def set_k(k):
    """Rebuild FFTSYN and EMIT32 for a different number of unshifted
    synthesis stages (K is baked into the code at assembly time, so the
    module-level pair has to be regenerated together).  K trades synthesis
    FFT headroom (each unshifted stage doubles the peak, and the stores are
    int16) against output word length; the identity SNR peaks out when the
    unshifted tail just fits, so this is measured, not assumed."""
    global FFTSYN_K, FFTSYN, EMIT32, KERNELS
    assert 0 < k < 10, k
    FFTSYN_K = k
    FFTSYN = _fft_synth()
    EMIT32 = _emit32()
    KERNELS = tuple((n, FFTSYN if n == "fftsyn" else EMIT32
                     if n == "emit32" else s) for n, s in KERNELS)
EMIT32 = _emit32()


def emit_scale():
    """EMIT32's Q13 multiplier for the current FFTSYN_K (N=1024).

    Frames leave fftsyn at x*win * 2**(K-log2 N); the OLA overlap sums them
    to 1.5x that; EMIT pre-shifts by 2**K and scales by S/2**EMIT_Q.  For a
    unity round trip: 1.5 * 2**(K-10) * 2**K * S = 2**EMIT_Q, so
    S = EMIT_SCALE * 2**(10 - 2K)  (EMIT_SCALE = 2/3 in Q13, tuned at K=5).
    For K > 5 the shift goes the other way; Q13 granularity keeps the gain
    error under 0.6% at K=8."""
    d = 10 - 2 * FFTSYN_K
    return EMIT_SCALE << d if d >= 0 else max(1, EMIT_SCALE >> -d)


def emit_acc_limit():
    """|acc| below which EMIT32's 32-bit clamp is exact: the shifted
    accumulator times SCALE must stay inside a signed 32-bit product, which
    also makes (product >> EMIT_Q) fit in int32 for the clamp."""
    return (2 ** 31 - 1) // (emit_scale() << FFTSYN_K)


def tables(box):
    n, hs = box.n, box.hs
    box.put(A_MAGPAD, bytes(16 + 2 * box.nb + 32))
    hann = [int(round(32767 * 0.5 * (1 - math.cos(2 * math.pi * k / n))))
            for k in range(n)]
    box.put(A_WIN, struct.pack(f"<{n}h", *hann))
    box.put(A_WSYN, struct.pack(f"<{n}h", *hann))
    tw = twiddles_q15(n)
    box.put(A_TW, struct.pack(f"<{2 * (n // 2)}h",
                              *[v for p in tw for v in p]))
    stab = P.build_sintab()
    box.put(A_STAB, struct.pack(f"<{len(stab)}h", *stab))
    atab = P.build_atantab()
    box.put(A_ATAB, struct.pack("<16H", *atab))


def set_hops(box, ha):
    """omega tables in Q16 turns: analysis advance b*Ha/N, synthesis b*Hs/N."""
    n, nb = box.n, box.nb
    oma = np.array([int(round(65536 * b * ha / n)) % 65536
                    for b in range(nb)], "<u2")
    oms = np.array([int(round(65536 * b * box.hs / n)) % 65536
                    for b in range(nb)], "<u2")
    box.put(A_OMA, oma.tobytes())
    box.put(A_OMS, oms.tobytes())
    return oma, oms


def spectrum(box, src, off, dst_re, dst_im, dst_mag, dst_ph):
    """window -> fft -> mag -> atan2 for the frame at src+off."""
    n, nb = box.n, box.nb
    fr = box.s16(src + 2 * off, n).astype(np.int32)
    box.put(A_FRM, fr.astype("<i2").tobytes())
    box.call("hann", r4=A_FRM, r5=A_WIN, r6=n)
    box.put(A_ZR, box.get(A_FRM, 2 * n))
    box.put(A_ZI, b"\0" * (2 * n))
    box.call("bitrev", r4=A_ZR, r5=A_ZI, r6=n)
    box.call("fftstages", r4=A_ZR, r5=A_ZI, r6=A_TW, r7=n, r8=n.bit_length() - 1)
    re = box.s16(A_ZR, nb)
    im = box.s16(A_ZI, nb)
    box.put(dst_re, re.astype("<i2").tobytes())
    box.put(dst_im, im.astype("<i2").tobytes())
    box.call("mag", r4=dst_re, r5=dst_im, r6=dst_mag, r7=nb)
    box.call("atan2", r4=dst_re, r5=dst_im, r6=dst_ph, r7=nb, r8=A_ATAB)
    return box.s16(dst_mag, nb), box.u16(dst_ph, nb)


def run_chain(x, r_target=1.0, ha=None, n=N, hs=HS, verbose=False,
              bypass=False, lock=False, lock_w=2, lock_ct=1):
    """... lock: peak-locking (PROP5). OFF by default since 2026-10-03:
    the fixed-point PROP5 port costs ~9 dB on the identity signal
    (39.3 dB without, 30.3 with) while the float model's lock does not --
    a port bug, not the algorithm (see notes). Keep the path wired for
    the hunt."""
    """Mono stretch by hs/ha (default ha = round(hs/r_target)). Returns the
    stretched int16 stream (pre-resample) plus the box (for step counts).

    bypass=True skips the phase engine and pushes the analysis spectrum
    straight back through the synthesis half: that isolates window/FFT/OLA
    scaling from the polar/propagate path."""
    box = Box(n, hs)
    tables(box)
    nb = box.nb
    assert n.bit_length() - 1 > FFTSYN_K, (n, FFTSYN_K)
    x = np.asarray(x, np.int32)
    box.put(A_XIN, x.astype("<i2").tobytes())
    if ha is None:
        ha = max(8, int(round(hs / r_target)))
    oma, oms = set_hops(box, ha)
    acc = np.zeros(n + hs, np.int32)
    box.put(A_ACC, acc.tobytes())
    nframes = (len(x) - n) // ha + 1
    outs = []
    first = True
    for m in range(nframes):
        # One analysis FFT per frame.  PH0/PH1 hold the previous and the
        # current frame's phase, MAG1 the current frame's magnitudes; the
        # slide at the bottom of the loop turns frame m into frame m-1 for
        # the next pass.  (PROP consumes ph0=frame m-1, ph1=frame m: the
        # current frame's own analysis hop is Ha.)
        spectrum(box, A_XIN, m * ha, A_RE, A_IM, A_MAG1, A_PH1)
        if first:
            box.put(A_PH0, box.get(A_PH1, 2 * nb))
            box.put(A_PREV, box.get(A_PH1, 2 * nb))
            first = False
        if bypass:
            box.put(A_ZR, box.get(A_RE, 2 * nb))      # original re/im
            box.put(A_ZI, box.get(A_IM, 2 * nb))
        else:
            box.call("peaks", r4=A_MAG1, r5=nb, r6=A_PK)
            box.call("nearest", r4=A_PK, r5=nb, r6=A_NEAR)
            box.call("prop", r4=A_PREV, r5=A_PH0, r6=A_PH1, r7=A_OMA,
                     r8=A_OMS, r9=A_NEW, r10=nb)
            if lock:
                # Region-of-influence locking (PROP5): a bin borrows its
                # peak's phase ramp only when it is inside the peak's main
                # lobe AND the peak dominates the spectrum 8 bins out, and the
                # offset it uses comes from last frame's phases, not this
                # frame's analysis phases -- a per-frame offset re-phases the
                # whole noise floor (-8 dB on broadband) and jumps every time
                # the peak migrates between bins (-23 dB on a glissando).
                box.put(A_PREVX, box.get(A_PREV, 2 * nb))
                box.call("prop5", r4=A_NEW, r5=A_PREVX, r6=A_NEAR, r7=A_MAGPAD,
                         r8=lock_w, r9=A_PREV, r10=nb, r11=lock_ct)
            else:
                box.put(A_PREV, box.get(A_NEW, 2 * nb))
            # polar -> complex with the CURRENT frame's magnitudes
            box.put(A_ZR, box.get(A_PREV, 2 * nb))
            box.call("sins", r4=A_ZR, r5=A_STAB, r6=nb, r7=A_SIN)
            box.call("coss", r4=A_ZR, r5=A_STAB, r6=nb, r8=A_COS)
            box.call("rect", r4=A_MAG1, r5=A_SIN, r6=A_COS, r7=A_RE,
                     r8=A_IM, r9=nb)
        box.call("mirr", r4=A_RE, r5=A_IM, r6=A_ZR, r7=A_ZI, r8=n)
        # conj-trick IFFT with the FORWARD twiddle table:
        #   conj(FFT(conj(X))) == IDFT(X)  (FFTSTAGES already scales 1/N)
        box.call("conj", r4=A_ZI, r5=n)
        box.call("bitrev", r4=A_ZR, r5=A_ZI, r6=n)
        box.call("fftsyn", r4=A_ZR, r5=A_ZI, r6=A_TW, r7=n,
                 r8=n.bit_length() - 1, r9=FFTSYN_K)
        box.call("conj", r4=A_ZI, r5=n)
        box.call("hann", r4=A_ZR, r5=A_WSYN, r6=n)
        box.put(A_ACC, acc.tobytes())
        box.call("ola32", r4=A_ACC, r5=A_ZR, r6=n)
        acc = np.frombuffer(box.get(A_ACC, 4 * (n + hs)), "<i4").copy()
        assert int(np.abs(acc).max()) < emit_acc_limit(), \
            (int(np.abs(acc).max()), emit_acc_limit())
        if m >= n // hs - 1:
            box.put(A_ACC, acc.tobytes())
            box.call("emit32", r3=emit_scale(), r4=A_ACC, r5=A_OUT, r6=hs)
            outs.append(box.s16(A_OUT, hs).astype(np.int32))
        acc = np.concatenate([acc[hs:], np.zeros(hs, np.int32)])
        box.put(A_PH0, box.get(A_PH1, 2 * nb))
        if verbose and m % 4 == 0:
            print(f"  frame {m}/{nframes}", flush=True)
    return np.concatenate(outs).astype(np.int32), box, ha


def shift_chain(x, semitones, n=N, hs=HS):
    """Stretch by hs/ha, then resample by the same ratio = pitch shift."""
    r = 2.0 ** (semitones / 12.0)
    y, box, ha = run_chain(x, r_target=r, n=n, hs=hs)
    rq = hs / ha
    step = int(round(rq * 65536))
    n_out = ((len(y) - 2) << 16) // step
    box.put(A_RSRC, y.astype("<i2").tobytes() + b"\0\0\0\0")
    box.call("resample", r4=A_RDST, r5=A_RSRC, r6=n_out, r7=step)
    return box.s16(A_RDST, n_out).astype(np.int32), rq, box


def snr(a, b, skip=0):
    a = np.asarray(a, np.float64)[skip:]
    b = np.asarray(b, np.float64)[skip:]
    k = min(len(a), len(b))
    e = a[:k] - b[:k]
    return 20 * math.log10(np.sqrt((b[:k] ** 2).mean())
                           / max(np.sqrt((e ** 2).mean()), 1e-9))


def identity_test(secs=0.5, amp=9000, freq=440.0):
    sr = 44100
    m = int(secs * sr)
    t = np.arange(m) / sr
    rng = np.random.default_rng(4)
    x = ((amp * np.sin(2 * math.pi * freq * t)
          + 0.45 * amp * np.sin(2 * math.pi * 1234.5 * t + 0.3)
          + rng.integers(-400, 400, m))).astype(np.int32)
    y, box, ha = run_chain(x, r_target=1.0, verbose=True)
    lat = (N // HS - 1) * HS
    k = min(len(y), len(x) - lat)
    s = snr(y, x[lat:lat + k], skip=N)
    tot = sum(box.steps.values())
    print(f"identity: ha={ha} out={len(y)} SNR {s:.1f} dB "
          f"(over {k - N} samples, latency {lat})")
    for kk, v in sorted(box.steps.items(), key=lambda kv: -kv[1])[:6]:
        print(f"   {kk:9s} {v / len(box.steps) / 1e3:8.1f} k/frame")
    print(f"   total {tot / 1e6:.1f} M instr for {len(y)} samples "
          f"-> {tot / len(y) * 44100 / 1e6:.0f} M instr/s of audio")
    return s, y, lat


def shift_test(st=2, secs=0.9, n=N, hs=HS):
    sr = 44100
    m = int(secs * sr)
    t = np.arange(m) / sr
    x = (9000 * np.sin(2 * math.pi * 440 * t)).astype(np.int32)
    y, rq, box = shift_chain(x, st, n=n, hs=hs)
    seg = y[3000:].astype(float)
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 18))
    f = np.fft.rfftfreq(1 << 18, 1 / sr)
    pk = f[sp.argmax()]
    want = 440 * rq
    exact = 440 * 2 ** (st / 12.0)
    near = sp[(f > want * 0.97) & (f < want * 1.03)].sum() / sp.sum()
    tot = sum(box.steps.values())
    print(f"shift {st:+d} st (R={rq:.4f}, ha={round(hs / rq)}): peak {pk:.2f} Hz "
          f"(want {want:.2f}, exact {exact:.2f})  on-note {near * 100:.1f}%  "
          f"{tot / 1e6:.0f} M instr")
    return abs(pk - exact) < 4.0 and near > 0.95


if __name__ == "__main__":
    if sys.argv[1:2] == ["shift"]:
        st = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        sys.exit(0 if shift_test(st) else 1)
    if sys.argv[1:2] == ["small"]:
        # quick, cheap proof of the whole path (fewer bins)
        s, y, lat = identity_test(secs=0.25)
        sys.exit(0 if s > 30 else 1)
    s, y, lat = identity_test()
    ok = s > 30
    print("RESULT:", "PASS" if ok else "FAIL", f"({s:.1f} dB)")
    sys.exit(0 if ok else 1)