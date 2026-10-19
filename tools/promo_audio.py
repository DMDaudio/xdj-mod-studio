"""Soundtrack for the promo clip: "Don't Come Back" from its STEMS file,
doing what each scene shows (beat jump back 4 beats, +2 semitone key shift
with tempo kept, vocals muted), then muxed into the video.

    .venv/bin/python tools/promo_audio.py [VIDEO]
Scene times follow tools/promo_video.py.
"""
import os
import subprocess
import sys
import tempfile
sys.path[:0] = ["tools"]
import numpy as np                       # noqa: E402
import stems4                            # noqa: E402

FS = 44100
SRC = os.environ.get("SRC", "dist/deck-tests/Get Lucky ESQUIRE (STEMS).wav")
START = float(os.environ.get("START", "49"))    # Pharrell's verse
BPM = float(os.environ.get("BPM", "125"))
# scene starts in the video (promo_video.py durations)
D = [2.6, 4.2, 3.4, 4.5, 3.6, 3.6, 3.6, 3.6, 2.6, 3.0]
T = np.cumsum([0] + D)
BJ, KS, ST, MORE = T[4], T[5], T[6], T[7]
TOTAL = T[-1]


def main(video):
    _, _, _, st, mix = stems4.read(SRC)
    mix = mix.reshape(-1, 2).astype(np.float32)
    rest = sum(st[k].reshape(-1, 2).astype(np.float32) for k in ("drums", "bass", "other"))
    n = int(TOTAL * FS)
    t = np.arange(n) / FS
    # BEAT JUMP scene (3 screens): back 16 beats when the << 16 screen shows,
    # forward 16 when >> 16 shows
    j16 = 16 * 60 / BPM
    b1, b2 = BJ + D[4] / 3, BJ + 2 * D[4] / 3
    src = START + t - np.where((t >= b1) & (t < b2), j16, 0.0)
    idx = (src * FS).astype(np.int64)
    out = mix[idx].copy()
    # STEMS scene (2 screens): vocals off when the muted screen shows -- the
    # deck plays the SUM of the other stems (not mix minus vocals)
    m0, m1 = int((ST + D[6] / 2) * FS), int(MORE * FS)
    out[m0:m1] = rest[idx[m0:m1]]
    # KEY SHIFT scene (2 screens): +2 then -2 semitones, tempo kept
    edits = [b1, b2, ST + D[6] / 2, MORE]
    import wave

    def shift(a0, a1, semis):
        k0, k1 = int(a0 * FS), int(a1 * FS)
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a.wav"), os.path.join(td, "b.wav")
            with wave.open(a, "wb") as w:
                w.setnchannels(2), w.setsampwidth(2), w.setframerate(FS)
                w.writeframes(np.clip(out[k0:k1], -32768, 32767).astype("<i2").tobytes())
            subprocess.run(["rubberband", "-3", "-p", str(semis), a, b], check=True,
                           capture_output=True)
            with wave.open(b) as w:
                sh = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(np.float32)
        sh = sh[: k1 - k0]
        out[k0:k0 + len(sh)] = sh
        edits.extend([a0, a1])
    shift(KS + 0.15, KS + D[5] / 2, 2)
    shift(KS + D[5] / 2, ST, -2)
    # short crossfades at every edit so nothing clicks
    for e in sorted(int(x * FS) for x in edits):
        f = 256
        if f < e < n - f:
            ramp = np.linspace(0, 1, 2 * f)[:, None]
            out[e - f:e + f] *= np.abs(1 - 2 * ramp) * 0.85 + 0.15
    fi, fo = int(0.3 * FS), int(1.5 * FS)
    out[:fi] *= np.linspace(0, 1, fi)[:, None]
    out[-fo:] *= np.linspace(1, 0, fo)[:, None]
    wav = "build/promo_audio.wav"
    import wave
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
