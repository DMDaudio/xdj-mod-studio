"""Soundtrack for the RECORDED promo (tools/promo_record.py): the emulated
deck's own audio output for the recorded scenes (so beat jumps are where the
deck made them), the file around it for the title cards, +2/-2 semitones
(rubberband, tempo kept) where the KEY SHIFT panel showed them, and the
deck's stem sum (drums+bass+other) where VOCALS was muted.

    .venv/bin/python tools/promo_audio_rec.py [VIDEO]
"""
import json
import os
import subprocess
import sys
import tempfile
import wave
sys.path[:0] = ["tools"]
import numpy as np                       # noqa: E402
import stems4                            # noqa: E402

FS = 44100
SRC = os.environ.get("SRC", "dist/deck-tests/Get Lucky ESQUIRE (STEMS).wav")
D = [2.6, 4.2, 3.4, 4.5, 3.6, 3.6, 3.6, 3.6, 2.6, 3.0]
TOTAL = sum(D)


def locate(x, ref, lo, hi):
    """file frame where the deck audio snippet x starts (search ref[lo:hi])"""
    y = ref[lo:hi]
    n = 1 << int(np.ceil(np.log2(len(y) + len(x))))
    r = np.fft.irfft(np.fft.rfft(y, n) * np.conj(np.fft.rfft(x, n)), n)
    return lo + int(np.argmax(r[: len(y) - len(x)]))


def rb(seg, semis):
    with tempfile.TemporaryDirectory() as td:
        a, b = os.path.join(td, "a.wav"), os.path.join(td, "b.wav")
        with wave.open(a, "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(FS)
            w.writeframes(np.clip(seg, -32768, 32767).astype("<i2").tobytes())
        subprocess.run(["rubberband", "-3", "-p", str(semis), a, b], check=True,
                       capture_output=True)
        with wave.open(b) as w:
            o = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(np.float32)
    o = o[: len(seg)]
    return np.vstack([o, seg[len(o):]]) if len(o) < len(seg) else o


def main(video):
    meta = json.load(open("build/promo_rec.json"))
    fps, r0, st = meta["fps"], meta["rec0"], meta["frames"]
    deck = np.load("build/promo_rec_audio.npy").astype(np.float32)
    _, _, _, stems, mix = stems4.read(SRC)
    mix = mix.reshape(-1, 2).astype(np.float32)
    rest = sum(stems[k].reshape(-1, 2).astype(np.float32) for k in ("drums", "bass", "other"))
    mono = mix.mean(1)
    # where the recording starts / ends in the file
    p0 = locate(deck[: FS, :].mean(1), mono, 0, 150 * FS)
    p1 = locate(deck[-FS:, :].mean(1), mono, 0, 150 * FS) + FS
    print("recording = file", p0 / FS, "..", p1 / FS, "s")
    n = int(TOTAL * FS)
    out = np.zeros((n, 2), np.float32)
    a0 = int(r0 * FS)
    out[:a0] = mix[p0 - a0:p0]
    L = min(len(deck), n - a0)
    out[a0:a0 + L] = deck[:L]
    tail = n - (a0 + L)
    out[a0 + L:] = mix[p1:p1 + tail]
    edits = [a0, a0 + L]
    # per-frame key / stem mask -> sample ranges
    def runs(field, pred):
        res, s0 = [], None
        for i, f in enumerate(st + [{field: 0}]):
            on = pred(f[field])
            if on and s0 is None:
                s0 = i
            if not on and s0 is not None:
                res.append((s0, i, st[s0][field])); s0 = None
        return [(a0 + int(a * FS / fps), a0 + int(b * FS / fps), v) for a, b, v in res]
    for s0, s1, v in runs("mask", lambda m: m & 1):
        q = locate(out[s0:s0 + FS // 2].mean(1), mono, max(0, p0 - 20 * FS), p1 + 20 * FS)
        out[s0:s1] = rest[q:q + (s1 - s0)]
        edits += [s0, s1]
    for s0, s1, v in runs("key", lambda k: k != 0):
        out[s0:s1] = rb(out[s0:s1], v)
        edits += [s0, s1]
        print("key", v, "from", s0 / FS, "to", s1 / FS)
    for e in sorted(edits):
        f = 256
        if f < e < n - f:
            ramp = np.linspace(0, 1, 2 * f)[:, None]
            out[e - f:e + f] *= np.abs(1 - 2 * ramp) * 0.85 + 0.15
    fi, fo = int(0.3 * FS), int(1.5 * FS)
    out[:fi] *= np.linspace(0, 1, fi)[:, None]
    out[-fo:] *= np.linspace(1, 0, fo)[:, None]
    wav = "build/promo_audio.wav"
    with wave.open(wav, "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(FS)
        w.writeframes(np.clip(out, -32768, 32767).astype("<i2").tobytes())
    res = video.replace(".mp4", "-with-song.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", wav,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest",
                    "-movflags", "+faststart", res], check=True)
    print("wrote", res)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dist/XDJ-Mod-Studio-promo-vertical.mp4")
