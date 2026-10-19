#!/usr/bin/env python3
"""pv.py -- phase-vocoder time-stretch + pitch shift prototype.

Pitch shift = PV stretch by 1/R then resample by R (same architecture
as the granular chain, but phase-propagated instead of spliced).
"""
import numpy as np

SR = 44100


def stft(x, n_fft=2048, hop=512):
    win = np.hanning(n_fft)
    frames = [x[i:i + n_fft] * win
              for i in range(0, len(x) - n_fft, hop)]
    return np.fft.rfft(np.array(frames), axis=1)


def istft(spec, hop=512):
    n_fft = (spec.shape[1] - 1) * 2
    win = np.hanning(n_fft)
    n_out = (len(spec) - 1) * hop + n_fft
    y = np.zeros(n_out)
    wsum = np.zeros(n_out)
    t = np.fft.irfft(spec, axis=1).real
    for m in range(len(spec)):
        y[m * hop:m * hop + n_fft] += t[m] * win
        wsum[m * hop:m * hop + n_fft] += win ** 2
    ok = wsum > 0.05
    y[ok] /= wsum[ok]
    y[~ok] = 0.0
    return y


def pv_stretch(x, s, n_fft=2048, hop=512, tr_thresh=0.35):
    """Stretch by factor s (out_len = len*s) with phase propagation.
    Transient reset: analysis frames with spectral flux above tr_thresh
    donate their phases directly (no propagation across attacks)."""
    spec = stft(x, n_fft, hop)
    mag = np.abs(spec)
    ph = np.angle(spec)
    omega = 2 * np.pi * hop * np.fft.rfftfreq(n_fft, 1.0 / SR) / SR
    flux = np.zeros(len(spec))
    flux[1:] = (np.abs(mag[1:] - mag[:-1]).sum(axis=1)
                / (mag[1:].sum(axis=1) + 1e-9))
    is_tr = flux > tr_thresh
    n_out = int(len(spec) * s)
    out_mag = np.zeros((n_out, mag.shape[1]))
    out_ph = np.zeros((n_out, mag.shape[1]))
    out_ph[0] = ph[0]
    nbins = mag.shape[1]
    for m in range(n_out):
        src = m / s
        i0 = int(np.floor(src))
        frac = src - i0
        i1 = min(i0 + 1, len(spec) - 1)
        i0 = min(i0, len(spec) - 1)
        out_mag[m] = mag[i0] * (1 - frac) + mag[i1] * frac
        trw = float(min(max((flux[i0] - 0.2) / 0.5, 0.0), 1.0))  # 0 calm..1 hit
        if trw > 0.5:
            # at attacks: nearest magnitudes (no smear) + mostly fresh phases
            out_mag[m] = mag[i0]
        if m > 0:
            a0 = mag[i0]
            # spectral peaks in this analysis frame (local maxima)
            pk = np.zeros(nbins, bool)
            pk[1:-1] = (a0[1:-1] > a0[:-2]) & (a0[1:-1] >= a0[2:])
            pk[0] = a0[0] >= a0[1]
            pk[-1] = a0[-1] > a0[-2]
            dphi = ph[i1] - ph[i0]
            inst = omega + np.angle(np.exp(1j * (dphi - omega)))
            prev = out_ph[m - 1]
            new = prev + inst
            # lock non-peak bins to nearest peak (keep analysis relations)
            if pk.any():
                nearest = np.zeros(nbins, int)
                last = 0
                for b in range(nbins):
                    if pk[b]:
                        last = b
                    nearest[b] = last
                nxt = nbins - 1
                for b in range(nbins - 1, -1, -1):
                    if pk[b]:
                        nxt = b
                    if abs(b - nxt) < abs(b - nearest[b]):
                        nearest[b] = nxt
                rel = ph[i0] - ph[i0][nearest]
                new = new[nearest] + rel
            # soft transient blend (trw=0: propagated; trw=1: fresh phases)
            if trw > 0:
                new = (1 - trw) * np.exp(1j * new) + trw * np.exp(1j * ph[i0])
                new = np.angle(new)
            out_ph[m] = new
    out = out_mag * np.exp(1j * out_ph)
    y = istft(out, hop)
    return y[:int(len(x) * s)]


def _aaf(cutoff, taps=127):
    t = np.arange(taps) - taps // 2
    h = np.sinc(2 * cutoff * t / 2) * np.hanning(taps)
    return h / h.sum()


def resample(x, ratio):
    if ratio > 1.0:
        x = np.convolve(x, _aaf(0.92 / ratio), mode="same")
    n_out = int(len(x) / ratio)
    pos = np.arange(n_out) * ratio
    i0 = np.floor(pos).astype(int)
    frac = pos - i0
    i1 = np.minimum(i0 + 1, len(x) - 1)
    return x[i0] * (1 - frac) + x[i1] * frac


def pv_shift(x, semitones):
    r = 2.0 ** (semitones / 12.0)
    # stretch longer (same pitch), then resample shorter (pitch up)
    y1 = pv_stretch(x, r)
    y2 = resample(y1, r)
    if len(y2) >= len(x):
        return y2[:len(x)]
    out = np.zeros(len(x))
    out[:len(y2)] = y2
    return out
