#!/usr/bin/env python3
"""keydetect.py -- musical key from audio (chroma + Krumhansl-Schmuckler).

    keydetect.py file.wav [...]          -> key, Camelot code, confidence
API: detect(x, sr) -> (tonic 0..11, minor bool, confidence)

Method: 8192-pt Hann STFT (hop 4096), bins 55..2000 Hz folded into 12
pitch classes with log-magnitude compression, summed over the clip, then
correlated against the 24 rotated Krumhansl-Kessler profiles. Confidence
= best correlation minus the runner-up (0..1-ish; <0.03 = ambiguous).
"""
import sys
import wave
import numpy as np

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# Camelot wheel: number for each tonic, major = B, minor = A
CAM_MAJ = {0: 8, 7: 9, 2: 10, 9: 11, 4: 12, 11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7}
CAM_MIN = {9: 8, 4: 9, 11: 10, 6: 11, 1: 12, 8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7}


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("16-bit PCM wav only")
    x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def chroma(x, sr, n=8192, hop=4096):
    """Peak-picked chroma: only local spectral maxima (>3% of the frame's
    strongest, 55..2000 Hz) count, at their parabola-interpolated
    frequency, weighted sqrt(mag). Log-compressing every bin instead
    smears leakage over all 12 classes (measured: 3/24 keys right)."""
    win = np.hanning(n)
    fr = np.fft.rfftfreq(n, 1.0 / sr)
    lo, hi = np.searchsorted(fr, 55), np.searchsorted(fr, 2000)
    c = np.zeros(12)
    for i in range(0, len(x) - n, hop):
        mag = np.abs(np.fft.rfft(x[i:i + n] * win))
        m = mag[lo:hi]
        if m.max() < 1e-6:
            continue
        k = np.nonzero((m[1:-1] > m[:-2]) & (m[1:-1] >= m[2:]) &
                       (m[1:-1] > 0.03 * m.max()))[0] + 1
        a_, b_, c_ = m[k - 1], m[k], m[k + 1]
        d = 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_ + 1e-12)
        f = (lo + k + d) * sr / n
        pc = np.round(12 * np.log2(f / 440.0) + 69).astype(int) % 12
        c += np.bincount(pc, weights=np.sqrt(b_), minlength=12)
    return c


def detect(x, sr):
    c = chroma(x, sr)
    c = (c - c.mean()) / (c.std() + 1e-12)
    scores = []
    for minor, prof in ((False, MAJ), (True, MIN)):
        p = (prof - prof.mean()) / prof.std()
        for t in range(12):
            scores.append((float(np.dot(c, np.roll(p, t)) / 12), t, minor))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    return best[1], best[2], best[0] - second[0]


def name(t, minor):
    return NOTES[t] + ("m" if minor else "")


def camelot(t, minor):
    return f"{(CAM_MIN if minor else CAM_MAJ)[t]}{'A' if minor else 'B'}"


if __name__ == "__main__":
    for f in sys.argv[1:]:
        x, sr = read_wav(f)
        t, mn, conf = detect(x, sr)
        print(f"{f}: {name(t, mn)}  ({camelot(t, mn)})  confidence {conf:.3f}")
