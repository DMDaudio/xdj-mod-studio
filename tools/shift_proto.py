#!/usr/bin/env python3
"""shift_proto.py -- granular pitch-shift prototype + quality metric.

Recipe (SoundTouch-style): resample (pitch+duration) then OLA time-stretch
back to constant duration. Metric (RX3-style): share of output energy on
the intended note for a 440 Hz probe tone.
"""
import sys
import numpy as np

SR = 44100


def _aaf(cutoff, taps=127):
    """Windowed-sinc lowpass, cutoff as fraction of Nyquist (0..1)."""
    t = np.arange(taps) - taps // 2
    h = np.sinc(2 * cutoff * t / 2) * np.hanning(taps)
    return h / h.sum()


def resample(x, ratio):
    """Linear-interp resample by ratio (out_len = len/ratio), with FIR
    anti-alias when downsampling (ratio > 1)."""
    if ratio > 1.0:
        h = _aaf(0.92 / ratio)
        x = np.convolve(x, h, mode="same")
    n_out = int(len(x) / ratio)
    pos = np.arange(n_out) * ratio
    i0 = np.floor(pos).astype(int)
    frac = pos - i0
    i1 = np.minimum(i0 + 1, len(x) - 1)
    return x[i0] * (1 - frac) + x[i1] * frac


def stretch(x, s, grain=2048, search=64, guide=None, bigwin=900):
    """Hann OLA time-stretch by factor s (out_len = len*s), with
    correlation-aligned hops (SOLA-lite). Anchored clock (no drift).
    guide=[offsets] forces hop offsets (stereo link). Returns (out, offs)."""
    hop_out = grain // 2
    hop_in = max(1, int(round(hop_out / s)))
    win = np.hanning(grain)
    n_out = int(len(x) * s) + grain
    y = np.zeros(n_out)
    wsum = np.zeros(n_out)
    ip = op = 0
    ideal = 0.0
    offs = []
    gi = 0
    eref = float(np.dot(x[:hop_out], x[:hop_out]) / max(1, hop_out)) + 1e-9
    while int(ideal) + grain + bigwin < len(x) and op + grain < n_out:
        if guide is not None and gi < len(guide):
            ip = int(ideal) + guide[gi]
            gi += 1
        elif search and op > 0:
            # hierarchical align to the written output tail: coarse on
            # decimated signal (catches bass periods), fine on full rate,
            # preferring quiet splice points (transient avoidance)
            tail = y[op:op + hop_out]
            tnorm = float(np.dot(tail, tail)) + 1e-9
            decim = 8
            xd = x[::decim]
            td = tail[::decim]
            hd = hop_out // decim
            tn = float(np.dot(td, td)) + 1e-9
            ck, cc = 0, -1e99
            cr = max(bigwin // decim, search // decim)
            for k in range(-cr, cr + 1):
                j = int(ideal) // decim + k
                if j < 0 or j + hd >= len(xd):
                    continue
                seg = xd[j:j + hd]
                c = float(np.dot(seg, td)) / (
                    np.sqrt(float(np.dot(seg, seg)) + 1e-9) * np.sqrt(tn))
                c -= 0.002 * abs(k)
                if c > cc:
                    cc, ck = c, k
            best_k, best_c = ck * decim, -1e99
            for k in range(ck * decim - decim, ck * decim + decim + 1):
                j = int(ideal) + k
                if j < 0 or j + hop_out >= len(x):
                    continue
                seg = x[j:j + hop_out]
                se = float(np.dot(seg, seg) / hop_out) + 1e-9
                c = float(np.dot(seg, tail)) / (
                    np.sqrt(float(np.dot(seg, seg)) + 1e-9) * np.sqrt(tnorm))
                c -= 0.35 * min(se / eref, 3.0)
                if c > best_c:
                    best_c, best_k = c, k
            ip = int(ideal) + best_k
            offs.append(best_k)
        else:
            ip = int(ideal)
            offs.append(0)
        y[op:op + grain] += x[ip:ip + grain] * win
        wsum[op:op + grain] += win
        ideal += hop_in
        op += hop_out
    wsum[wsum < 1e-6] = 1.0
    return (y / wsum)[:int(len(x) * s)], offs


def shift(x, semitones):
    r = 2.0 ** (semitones / 12.0)
    # resample by r: pitch*r, length/r; stretch by r: length back, pitch kept
    y1 = resample(x, r)
    y2, _ = stretch(y1, r)
    # fix exact length
    if len(y2) >= len(x):
        return y2[:len(x)]
    out = np.zeros(len(x))
    out[:len(y2)] = y2
    return out


def shift_stereo(x, semitones, search=160):
    """Stereo-linked shift: hop offsets computed on mid, applied to L/R."""
    r = 2.0 ** (semitones / 12.0)
    mid = x.mean(axis=1)
    y1m = resample(mid, r)
    _, offs = stretch(y1m, r, search=search)
    outs = []
    for ch in range(x.shape[1]):
        y1 = resample(x[:, ch], r)
        y2, _ = stretch(y1, r, search=search, guide=offs)
        outs.append(y2)
    n = min(len(x), min(map(len, outs)))
    return np.stack([o[:n] for o in outs], axis=1)


def dopshift(x, semitones, depth=2048):
    """Single-stage pitch shift via dual ramping delays (Doppler):
    delay D ramps so the read head advances at ratio R per output sample;
    two lines run half a ramp apart, equal-power crossfaded. Jumps hide
    in the faded line. No resample, no block expansion."""
    r = 2.0 ** (semitones / 12.0)
    n = len(x)
    hist = np.zeros(n + 2 * depth + 16)
    y = np.zeros(n)
    slope = 1.0 - r  # dD/dn; up (r>1): delay shrinks
    period = depth / abs(slope) if slope != 0 else float(n + 1)
    dmax = float(depth)
    for i in range(n):
        hist[dly0 := depth + i] = x[i] if i < n else 0.0
        ph = (i % period) / period if period > 0 else 0.0
        g2 = ph * ph * (3 - 2 * ph)
        g1 = 1.0 - g2
        d1 = dmax + slope * (i % period)
        d2 = dmax + slope * ((i + period / 2) % period)
        s1 = _readlin(hist, depth + i - d1)
        s2 = _readlin(hist, depth + i - d2)
        y[i] = g1 * s1 + g2 * s2
    return y


def _readlin(hist, pos):
    i = int(np.floor(pos))
    f = pos - i
    n = len(hist)
    i = max(0, min(i, n - 2))
    return hist[i] * (1 - f) + hist[i + 1] * f


def note_share(y, target, tol=0.02):
    sp = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    fr = np.fft.rfftfreq(len(y), 1.0 / SR)
    tot = sp.sum()
    near = sp[(fr >= target * (1 - tol)) & (fr <= target * (1 + tol))].sum()
    peak = fr[sp.argmax()]
    return near / tot, peak


def main():
    t = np.arange(2 * SR) / SR
    probe = np.sin(2 * np.pi * 440 * t).astype(float)
    # crude music-ish: chord + transient clicks
    music = (np.sin(2 * np.pi * 220 * t) * 0.5
             + np.sin(2 * np.pi * 277 * t) * 0.3
             + np.sin(2 * np.pi * 330 * t) * 0.2)
    clicks = np.zeros_like(t)
    clicks[::SR // 4] = 1.0
    music = music + clicks * 0.5
    for s in (7, -7, 12):
        for name, x, f0 in (("tone440", probe, 440.0), ("music", music, 220.0)):
            y = shift(x, s)
            tgt = f0 * 2.0 ** (s / 12.0)
            share, peak = note_share(y, tgt)
            print(f"s={s:+3d} {name:8s} target={tgt:7.1f}Hz peak={peak:7.1f}Hz "
                  f"on-note={share * 100:5.1f}%")
    print("ok")


if __name__ == "__main__":
    main()
