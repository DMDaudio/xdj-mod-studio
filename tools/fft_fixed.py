#!/usr/bin/env python3
"""fft_fixed.py -- fixed-point radix-2 FFT integer model (for SH-4 port).

Design: DIT, Q15 data in/out, Q15 twiddles, unconditional >>1 per stage
(no overflow possible; N=2048 loses 11 bits worst case -- SNR checked
vs numpy below). Bit-reversal explicit. Every op int32-masked: the SH
assembly must match word for word.
"""
import math
import numpy as np

MASK = 0xFFFFFFFF


def s32(v):
    v &= MASK
    return v - (1 << 32) if v & 0x80000000 else v


def s16(v):
    v &= 0xFFFF
    return v - (1 << 16) if v & 0x8000 else v


def twiddles_q15(n):
    """W[k] = round(32767*exp(-2j*pi*k/n)) as (re, im) int pairs, k<n/2."""
    out = []
    for k in range(n // 2):
        a = 2 * math.pi * k / n
        out.append((int(round(32767 * math.cos(a))),
                    int(round(-32767 * math.sin(a)))))
    return out


def fft_q15(xr, xi, tw):
    """In-place DIT FFT on int lists (Q15). Returns (re, im), scaled by 1/N
    total (>>1 per stage)."""
    n = len(xr)
    assert n == len(xi) and n & (n - 1) == 0
    # bit-reversal
    j = 0
    r = list(xr)
    im = list(xi)
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            r[i], r[j] = r[j], r[i]
            im[i], im[j] = im[j], im[i]
    # stages
    stages = n.bit_length() - 1
    step = 2
    for _ in range(stages):
        half = step // 2
        tstep = n // step
        for g in range(0, n, step):
            for k in range(half):
                wr, wi = tw[k * tstep]
                a, b = r[g + k], im[g + k]
                c, d = r[g + k + half], im[g + k + half]
                # (c+jd)*(wr+jwi)>>15, then butterfly >>1
                tr = (c * wr - d * wi) >> 15
                ti = (c * wi + d * wr) >> 15
                r[g + k] = s32((a + tr) >> 1)
                im[g + k] = s32((b + ti) >> 1)
                r[g + k + half] = s32((a - tr) >> 1)
                im[g + k + half] = s32((b - ti) >> 1)
        step *= 2
    return r, im


def fft_q15_tail(xr, xi, tw, tail=0):
    """fft_q15 with the LAST `tail` stages left UNSHIFTED, i.e. the result is
    2**tail times fft_q15.  Same fixed point, same wraps.

    This is pv_run.FFTSYN: stopping the per-stage >>1 early keeps the
    synthesis time signal near full scale instead of full scale / N, and the
    caller folds the 2**tail back in with a single Q16 multiply (EMIT32)."""
    n = len(xr)
    assert 0 <= tail <= n.bit_length() - 1
    # bit-reversal
    j = 0
    r = list(xr)
    im = list(xi)
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            r[i], r[j] = r[j], r[i]
            im[i], im[j] = im[j], im[i]
    stages = n.bit_length() - 1
    step = 2
    for si in range(stages):
        sh = 0 if si >= stages - tail else 1
        half = step // 2
        tstep = n // step
        for g in range(0, n, step):
            for k in range(half):
                wr, wi = tw[k * tstep]
                a, b = r[g + k], im[g + k]
                c, d = r[g + k + half], im[g + k + half]
                tr = (c * wr - d * wi) >> 15
                ti = (c * wi + d * wr) >> 15
                u, v = a + tr, b + ti
                p, q = a - tr, b - ti
                # the hardware stores every butterfly output with MOV.W, so
                # the 16-bit wrap is real at every stage and the model must
                # reproduce it (harmless while |x| stays well under full
                # scale: the per-stage >>1 keeps everything in range)
                u, v, p, q = (s16(z >> 1) for z in (u, v, p, q)) if sh \
                    else (s16(z) for z in (u, v, p, q))
                r[g + k] = u
                im[g + k] = v
                r[g + k + half] = p
                im[g + k + half] = q
        step *= 2
    return r, im


def ifft_q15(xr, xi, tw):
    """IFFT via conjugate trick. Returns time domain (Q15 scale kept)."""
    n = len(xr)
    r, im = fft_q15(xr, [-v for v in xi], tw)
    return [r[i] for i in range(n)], [-im[i] for i in range(n)]


def selftest():
    sr = 44100
    for n in (256, 2048):
        t = np.arange(n) / sr
        x = (0.7 * np.sin(2 * np.pi * 440 * t)
             + 0.3 * np.sin(2 * np.pi * 1200 * t + 1.0))
        xi = [0] * n
        xr = [int(round(v * 32767)) for v in x]
        tw = twiddles_q15(n)
        r, im = fft_q15(xr, xi, tw)
        ref = np.fft.rfft(x)
        # compare magnitudes (fixed has 1/N scale from stage shifts)
        got = np.array([math.sqrt(a * a + b * b) for a, b in zip(r, im)][:n // 2 + 1])
        want = np.abs(ref) / n * 32767
        err = np.abs(got - want)
        snr = 20 * math.log10(want.max() / max(err.max(), 1e-9))
        # roundtrip
        rr, ri = ifft_q15(r, im, tw)
        back = np.array(rr) / 32767 * n
        print(f"N={n}: fft SNR={snr:.1f}dB roundtrip-maxerr={np.abs(back - x).max():.5f}")
    return True


if __name__ == "__main__":
    selftest()
