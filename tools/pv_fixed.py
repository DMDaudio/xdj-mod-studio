#!/usr/bin/env python3
"""pv_fixed.py -- integer reference model for the SH-4 key-shift core.

Bit-exact spec for the assembly: every op is int32 with explicit
shifts/rounding, so shasm output can be compared word for word.
Scope so far: Q15 Hann window, Q16 linear-interp resample, windowed
overlap-add with Q15 normalisation. (FFT / phase math come later.)

  hann_q15(n)            -> list[int]   round(32767 * 0.5*(1-cos(2pi k/n)))
  resample_q16(x, step)  -> list[int]   step = ratio in Q16 (65536 = 1.0)
  ola_q15(frames, hop)   -> list[int]   frames already windowed by hann_q15
"""
import math

MASK = 0xFFFFFFFF


def s32(v):
    v &= MASK
    return v - (1 << 32) if v & 0x80000000 else v


def hann_q15(n):
    # periodic Hann (matches np.hanning phase closely enough; the table
    # is what ships, so THIS table is the truth, not the float one)
    return [int(round(32767 * 0.5 * (1 - math.cos(2 * math.pi * k / n))))
            for k in range(n)]


def resample_q16(x, step, n_out=None):
    """out[i] = a + (((b-a) * f15) >> 15), a=x[p>>16], b=x[(p>>16)+1],
    f15 = (p&0xFFFF)>>1, p = i*step (Q16). Fits int32 for s16 input
    (17-bit diff * 15-bit frac). Arithmetic shift = floor. n_out defaults
    to the count that never reads past x[-1], so no clamp is needed."""
    n = len(x)
    if n_out is None:
        n_out = ((n - 1) << 16) // step
    out = []
    p = 0
    for _ in range(n_out):
        i = p >> 16
        f = (p & 0xFFFF) >> 1
        a = x[i]
        b = x[i + 1]
        out.append(s32(a + (((b - a) * f) >> 15)))
        p = (p + step) & MASK
    return out


def ola_add_q15(acc, frame, win):
    """One frame of the SH-4 kernel: acc[k] += (frame[k]*win[k]) >> 15,
    frame s16, win s16 (Q15 Hann), acc int32. Mutates and returns acc."""
    for k in range(len(frame)):
        acc[k] = s32(acc[k] + ((frame[k] * win[k]) >> 15))
    return acc


def ola_q15(frames, hop):
    """Overlap-add of pre-windowed frames (each sample * hann >> 15),
    result scaled by the constant Hann^2 overlap gain in Q15."""
    n = len(frames[0])
    win = hann_q15(n)
    wsq = [(w * w) >> 15 for w in win]
    total = (len(frames) - 1) * hop + n
    acc = [0] * total
    norm = [0] * total
    for m, fr in enumerate(frames):
        for k in range(n):
            acc[m * hop + k] += (fr[k] * win[k]) >> 15
            norm[m * hop + k] += wsq[k]
    out = []
    for a, w in zip(acc, norm):
        out.append(0 if w < 1638 else s32((a << 15) // w))  # 1638 ~ 0.05 in Q15
    return out


if __name__ == "__main__":
    import numpy as np
    h = hann_q15(2048)
    ref = 32767 * np.hanning(2049)[:2048]
    print("hann max err vs float:", max(abs(a - b) for a, b in zip(h, ref)))
    t = [int(10000 * math.sin(2 * math.pi * 440 * i / 44100)) for i in range(4410)]
    r = resample_q16(t, int(round(2 ** (2 / 12) * 65536)))
    print("resample len", len(r), "first", r[:6])


# ---------------------------------------------------------------- FFT
def twiddles_q15(n):
    """Forward twiddles w_k = exp(-2*pi*i*k/n), k < n/2, as (re, im) s16."""
    return [(int(round(32767 * math.cos(2 * math.pi * k / n))),
             int(round(-32767 * math.sin(2 * math.pi * k / n))))
            for k in range(n // 2)]


def bitrev_perm(n):
    bits = n.bit_length() - 1
    return [int(format(i, f"0{bits}b")[::-1], 2) for i in range(n)]


def fft_q15(xs, tw, shift=1):
    """Radix-2 DIT, input ALREADY in bit-reversed order. xs: list of
    [re, im] s16 (|component| <= 16383 keeps every stage inside s16).
    Every butterfly shifts >> shift (1 = forward, result FFT(x)/N;
    0 = unscaled, for the inverse with conjugated twiddles).
      t = (b * w) >> 15 (each component floor-shifted after the 2 products)
      a' = (a + t) >> 1 ; b' = (a - t) >> 1
    Mutates and returns xs."""
    n = len(xs)
    half = 1
    while half < n:
        stride = n // (2 * half)
        for i in range(0, n, 2 * half):
            for j in range(half):
                wr, wi = tw[j * stride]
                a = xs[i + j]
                b = xs[i + j + half]
                tr = (b[0] * wr - b[1] * wi) >> 15
                ti = (b[0] * wi + b[1] * wr) >> 15
                xs[i + j] = [(a[0] + tr) >> shift, (a[1] + ti) >> shift]
                xs[i + j + half] = [(a[0] - tr) >> shift, (a[1] - ti) >> shift]
        half *= 2
    return xs


# ------------------------------------------------- two-for-one stereo
def split_q15(z):
    """Z = FFT(L + iR) (N complex). Returns (Lk, Rk), k = 0..N/2, as
    [re, im] lists.  Lk = (Zk + conj Z[N-k]) >> 1,
    Rk = (Zk - conj Z[N-k]) / 2i  (>> 1)."""
    n = len(z)
    L, R = [], []
    for k in range(n // 2 + 1):
        a = z[k]
        b = z[(n - k) & (n - 1)]
        L.append([(a[0] + b[0]) >> 1, (a[1] - b[1]) >> 1])
        R.append([(a[1] + b[1]) >> 1, (b[0] - a[0]) >> 1])
    return L, R


def merge_q15(L, R, n):
    """Inverse of split for real L, R: Z_k = L_k + i R_k with the
    Hermitian extension for k > N/2. Second write first so k=0, N/2
    keep the k-formula. No shift (caller keeps |values| <= 16383)."""
    z = [[0, 0] for _ in range(n)]
    for k in range(n // 2 + 1):
        lr, li = L[k]
        rr, ri = R[k]
        m = (n - k) & (n - 1)
        z[m] = [lr + ri, rr - li]
        z[k] = [lr - ri, li + rr]
    return z
