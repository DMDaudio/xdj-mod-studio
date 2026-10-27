#!/usr/bin/env python3
"""ks_buzz.py CTL.wav SHIFT.wav -- the key shifter's block buzz: energy at
the 16-sample block rate (44100/16 = 2756 Hz) and its harmonics, relative to
the neighbouring spectrum, per channel. A clean shifter shows no peak (ratio
~1); a per-block glitch (2026-10-25: wrong history sample / reads past the
valid frames) shows as a spike, x10-x1000."""
import sys
import wave
import numpy as np


def rd(p):
    w = wave.open(p)
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, w.getnchannels())
    return a.astype(float), w.getframerate()


def buzz(x, fs):
    n = 1 << int(np.log2(len(x)))
    spec = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / fs)
    out = []
    for h in (1, 2, 3):
        f0 = fs / 16 * h
        i = int(round(f0 / (f[1] - f[0])))
        peak = spec[i - 3:i + 4].max()
        ref = np.median(np.r_[spec[i - 60:i - 10], spec[i + 10:i + 60]])
        out.append(peak / max(ref, 1e-9))
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        x, fs = rd(p)
        for c, name in ((0, "L"), (1, "R")):
            b = buzz(x[:, c], fs)
            print(f"{p.split('/')[-1]:14s} {name}: block-rate peak x{b[0]:.1f}  "
                  f"2nd x{b[1]:.1f}  3rd x{b[2]:.1f}")
