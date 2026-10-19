#!/usr/bin/env python3
"""pv_chain_track.py SRC.wav START SECS SEMITONES -- run a real track window
through the SH-4 float chain (emulated) and write it next to the same
window of the rubberband ref and the accepted pv.py render for A/B.

  tools/pv_chain_track.py work/shift_samples/oot_+0.wav 8 8 2
"""
import sys
import wave
import numpy as np
sys.path.insert(0, "tools")
import pv_chain as P

SR = 44100


def load(path, start, secs):
    with wave.open(path) as w:
        w.setpos(int(start * w.getframerate()))
        a = np.frombuffer(w.readframes(int(secs * w.getframerate())), "<i2")
        ch = w.getnchannels()
    a = a.reshape(-1, ch)
    return a[:, 0].copy(), a[:, -1].copy()


def save(path, l, r):
    m = min(len(l), len(r))
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.column_stack([l[:m], r[:m]]).astype("<i2").tobytes())


if __name__ == "__main__":
    src, start, secs, st = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
    xl, xr = load(src, start, secs)
    ol, orr, Rq, box = P.shift_chain(xl, xr, st)
    tag = f"{int(start)}s_{int(secs)}s_{st:+d}"
    out = "work/shift_samples/"
    save(out + f"cmp_{tag}_chainlock.wav", ol, orr)
    save(out + f"cmp_{tag}_orig.wav", xl, xr)
    base = src.rsplit("_+0.wav", 1)[0]
    for name, suffix in (("ref", "+2ref"), ("pvm", "+2m")):
        try:
            l, r = load(f"{base}_{suffix}.wav", start, secs)
            save(out + f"cmp_{tag}_{name}.wav", l, r)
        except Exception as e:
            print("no", suffix, e)
    tot = sum(box.steps.values())
    print(f"done: {tot / 1e6:.0f} M instr, {tot / secs / 1e6:.0f} M instr/s of audio")
