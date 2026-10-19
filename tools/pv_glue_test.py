#!/usr/bin/env python3
"""pv_glue_test.py -- bit-check the pv_run.py glue kernels against models.

pv_phase.py already bit-checks the phase engine (peaks..rect) and the scaled
analysis FFT.  This file covers the four kernels that only exist in
pv_run.py:

  FFTSYN  synthesis FFT = the analysis butterfly with the per-stage >>1
          dropped from the last K stages, so the result is 2**K larger.
          Compared against fft_fixed.fft_q15_tail (int16 wrap on every
          unshifted store, exactly like the MOV.W in the kernel).
  MIRR    Hermitian mirror of a real spectrum (out[N-k] = conj(in[k]),
          bins 0 and N/2 get imag = 0).
  OLA32   int32 accumulator += sext16(frame).
  EMIT32  out = clamp((acc * SCALE) >> 16, -32768, 32767), floor rounding,
          built from shlr16/shll16/or (no 32-bit overflow, no SHAD).

Run: .venv/bin/python tools/pv_glue_test.py
"""
import struct
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import numpy as np
from fft_fixed import twiddles_q15, fft_q15_tail, s16
import pv_run as R
from pv_run import Box, FFTSYN_K

A_TW, A_RE, A_IM = R.A_TW, R.A_RE, R.A_IM
A_ZR, A_ZI, A_ACC, A_OUT = R.A_ZR, R.A_ZI, R.A_ACC, R.A_OUT
A_XIN = R.A_XIN


def s16arr(blob, n):
    return list(struct.unpack(f"<{n}h", blob))


# ------------------------------------------------------------------ FFT test
def test_fftsyn(rng):
    # NOTE: K is NOT a runtime argument.  pv_run._fft_synth() unrolls exactly
    # K stages at assembly time and ignores r9, so the only K that exists is
    # FFTSYN_K, and the transform needs STAGES > K (at least one shifted
    # stage) or the entry loop underflows.  Passing any other K silently
    # computes a different number of stages.
    bad = 0
    # K = 8 (FFTSYN_K): the transform needs at least one shifted stage,
    # so n >= 2^(K+1); smaller n are invalid configurations, not
    # failures (2026-10-10 note).
    ns = (1024, 2048)
    for n in ns:
        k = FFTSYN_K
        assert n.bit_length() - 1 > k, (n, k)
        tw = twiddles_q15(n)
        amp = 3000 if n >= 1024 else 30000
        box = Box(n, R.HS)
        box.put(A_TW, struct.pack(f"<{2 * (n // 2)}h",
                                  *[v for p in tw for v in p]))
        for t in range(2 if n > 256 else 4):
            xr = [int(v) for v in rng.integers(-amp, amp, n)]
            xi = [int(v) for v in rng.integers(-amp, amp, n)]
            box.put(A_ZR, struct.pack(f"<{n}h", *xr))
            box.put(A_ZI, struct.pack(f"<{n}h", *xi))
            box.call("bitrev", r4=A_ZR, r5=A_ZI, r6=n)
            box.call("fftsyn", r4=A_ZR, r5=A_ZI, r6=A_TW, r7=n,
                     r8=n.bit_length() - 1, r9=k)
            gr = s16arr(box.get(A_ZR, 2 * n), n)
            gi = s16arr(box.get(A_ZI, 2 * n), n)
            wr, wi = fft_q15_tail(list(xr), list(xi), tw, k)
            wr = [s16(v) for v in wr]
            wi = [s16(v) for v in wi]
            if gr != wr or gi != wi:
                bad += 1
                if bad < 4:
                    i = next(j for j in range(n) if gr[j] != wr[j])
                    print(f"  FFTSYN MISMATCH n={n} k={k} at {i}: "
                          f"got {gr[i]},{gi[i]} want {wr[i]},{wi[i]}")
    assert bad == 0, f"FFTSYN: {bad} mismatches"
    print(f"FFTSYN: exact vs fft_q15_tail for N={ns} at the compiled K="
          f"{FFTSYN_K} (16-bit wrap on the unshifted tail stores)")


# ------------------------------------------------------------------ MIRR test
def model_mirr(re, im, n):
    orr = [0] * n
    oii = [0] * n
    for k in range(1, n // 2):
        orr[k], oii[k] = re[k], im[k]
        orr[n - k], oii[n - k] = re[k], -im[k]
    orr[0], oii[0] = re[0], 0
    orr[n // 2], oii[n // 2] = re[n // 2], 0
    return orr, oii


def test_mirr(rng):
    bad = 0
    for n in (8, 16, 1024, 64):
        nb = n // 2 + 1
        for t in range(4):
            re = [int(v) for v in rng.integers(-32768, 32767, nb)]
            im = [int(v) for v in rng.integers(-32768, 32767, nb)]
            box = Box(n, R.HS)
            box.put(A_RE, struct.pack(f"<{nb}h", *re))
            box.put(A_IM, struct.pack(f"<{nb}h", *im))
            box.call("mirr", r4=A_RE, r5=A_IM, r6=A_ZR, r7=A_ZI, r8=n)
            gr = s16arr(box.get(A_ZR, 2 * n), n)
            gi = s16arr(box.get(A_ZI, 2 * n), n)
            wr, wi = model_mirr(re, im, n)
            if gr != wr or gi != wi:
                bad += 1
                if bad < 3:
                    i = next(j for j in range(n) if gr[j] != wr[j])
                    print(f"  MIRR MISMATCH n={n} at {i}: "
                          f"got {gr[i]},{gi[i]} want {wr[i]},{wi[i]}")
    assert bad == 0, f"MIRR: {bad} mismatches"
    print("MIRR: exact vs Hermitian-mirror model (N=8,16,64,1024)")


# ------------------------------------------------------------------ OLA test
def test_ola(rng):
    bad = 0
    for n in (1, 3, 64, 1024):
        for t in range(4):
            acc = [int(v) for v in rng.integers(-2 ** 24, 2 ** 24, n)]
            fr = [int(v) for v in rng.integers(-32768, 32767, n)]
            box = Box(n, R.HS)
            box.put(A_ACC, struct.pack(f"<{n}i", *acc))
            box.put(A_ZR, struct.pack(f"<{n}h", *fr))
            box.call("ola32", r4=A_ACC, r5=A_ZR, r6=n)
            gr = list(struct.unpack(f"<{n}i", box.get(A_ACC, 4 * n)))
            wr = [a + f for a, f in zip(acc, fr)]
            if gr != wr:
                bad += 1
                if bad < 3:
                    i = next(j for j in range(n) if gr[j] != wr[j])
                    print(f"  OLA MISMATCH n={n} at {i}: {gr[i]} vs {wr[i]}")
    assert bad == 0, f"OLA32: {bad} mismatches"
    print("OLA32: exact (int32 accumulate, sign-extended s16 frame)")


# ---------------------------------------------------------------- EMIT test
def model_emit(acc, scale_q16):
    """out = clamp((acc << K) * scale >> EMIT_Q)."""
    return [max(-32768, min(32767, (v << FFTSYN_K) * scale_q16 >> R.EMIT_Q))
            for v in acc]


# EMIT32 shifts the accumulator left by FFTSYN_K before the multiply, so the
# kernel is exact while |acc| * SCALE * 2**K stays inside a signed 32-bit
# product (which also keeps the >> EMIT_Q result inside int32 for the clamp).
EMIT_ACC_LIMIT = R.emit_acc_limit()


def test_emit(rng):
    bad = 0
    edge = [0, 1, -1, 32767, -32768, 32768, -32769, 1536, -1536, 64, -64,
            EMIT_ACC_LIMIT - 1, -EMIT_ACC_LIMIT + 1,
            EMIT_ACC_LIMIT, -EMIT_ACC_LIMIT]
    for n in (1, 5, 256):
        for scale in (R.EMIT_SCALE, 32768, 65535, 1, 0xFFFF, 2731):
            lim = min(EMIT_ACC_LIMIT, (2 ** 31 - 1) // (scale << FFTSYN_K))
            acc = [int(v) for v in rng.integers(-lim + 1, lim, n)]
            fit = [e for e in edge if abs(e) < lim][:n]
            acc[:len(fit)] = fit
            box = Box(n, R.HS)
            box.put(A_ACC, struct.pack(f"<{n}i", *acc))
            box.call("emit32", r3=scale, r4=A_ACC, r5=A_OUT, r6=n)
            gr = s16arr(box.get(A_OUT, 2 * n), n)
            wr = model_emit(acc, scale)
            if gr != wr:
                bad += 1
                if bad < 4:
                    i = next(j for j in range(n) if gr[j] != wr[j])
                    print(f"  EMIT MISMATCH n={n} scale={scale} at {i}: "
                          f"acc={acc[i]} got {gr[i]} want {wr[i]}")
    # every clamp boundary and the chain's own operating point
    probe = list(range(-40, 41)) + [-1536, 1536, -3000, 3000,
                                    -EMIT_ACC_LIMIT + 1, EMIT_ACC_LIMIT - 1]
    box = Box(len(probe), R.HS)
    box.put(A_ACC, struct.pack(f"<{len(probe)}i", *probe))
    box.call("emit32", r3=R.EMIT_SCALE, r4=A_ACC, r5=A_OUT, r6=len(probe))
    gr = s16arr(box.get(A_OUT, 2 * len(probe)), len(probe))
    wr = model_emit(probe, R.EMIT_SCALE)
    if gr != wr:
        bad += 1
        for a, g, w in zip(probe, gr, wr):
            if g != w:
                print(f"  EMIT chain-point mismatch acc={a}: {g} want {w}")
    # the output must NOT be quantised to a multiple of 2**FFTSYN_K any more
    odd = sum(1 for v in gr if v % (1 << FFTSYN_K))
    assert odd > len(probe) // 2, f"output still on a {1 << FFTSYN_K} grid"
    assert bad == 0, f"EMIT32: {bad} mismatches"
    print(f"EMIT32: exact (acc<<{FFTSYN_K} * Q13 scale, >>{R.EMIT_Q}, floor, "
          f"clamp); |acc| < {EMIT_ACC_LIMIT} covers the chain's ~1500 peak, "
          f"output off the 2**{FFTSYN_K} grid")


if __name__ == "__main__":
    rng = np.random.default_rng(11)
    test_fftsyn(rng)
    test_mirr(rng)
    test_ola(rng)
    test_emit(rng)
    print(f"PV GLUE: PASS (fftsyn K={FFTSYN_K}, mirr, ola32, emit32)")
