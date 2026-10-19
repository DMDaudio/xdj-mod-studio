#!/usr/bin/env python3
"""version_remix_test.py -- prove the version-file re-mix (the VSEL branch
in tools/stems/sm.c): after sequential deck-style reads under one version,
switching versions rewrites the already-buffered bytes with the new
version's bytes IN PLACE (version 0 restores the plain mix bit-exact).
Builds a small 2-version file (instrumental/acapella from test tones, no
rubberband), streams chunks under version A, flips to B, dumps the arena;
every buffered byte inside the sample data must equal the new version's
byte (or the raw mix for B == 0), bytes outside untouched. File-position
discipline is enforced by the helper (exit 2)."""
import os
import subprocess
import sys
import wave
sys.path[:0] = [".", "tools"]
import numpy as np                                     # noqa: E402

ROOT = os.path.join(os.environ.get("TMPDIR", "/tmp"), "vremix")
F = os.path.join(ROOT, "V versions.wav")
EXE = "build/stems/remix_check"


def make_file():
    if os.path.exists(F):
        return
    os.makedirs(ROOT, exist_ok=True)
    import versionfile as VF
    sr, secs = 44100, 15
    t = np.arange(sr * secs) / sr
    mixp = os.path.join(ROOT, "mix.wav")
    x = (0.2 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
    with wave.open(mixp, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.repeat(x, 2).tobytes())
    stems = []
    for i, f0 in enumerate((110, 330, 550, 880)):
        p = os.path.join(ROOT, f"s{i}.wav")
        y = (0.2 * np.sin(2 * np.pi * f0 * t) * 32767).astype(np.int16)
        with wave.open(p, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(np.repeat(y, 2).tobytes())
        stems.append(p)
    VF.build(mixp, F, stems=stems)


make_file()
import versionfile as VF                                # noqa: E402
d_off, d_len, V = VF.read_versions(F)
raw = open(F, "rb").read()
names = [k for k in V if k != "mix"]
print("versions:", names)
r = subprocess.run(["cc", "-O2", "-DSM_NATIVE", "-o", EXE,
                    "tools/stems/remix_check.c", "tools/stems/sm.c"],
                   capture_output=True, text=True)
if r.returncode:
    print(r.stderr); print("RESULT: FAIL (build)"); sys.exit(1)

cases = [
    # (vA, vB): mix->v1, v1->v2, v2->mix, v1->mix, v2->v1
    (0, 1), (1, 2), (2, 0), (1, 0), (2, 1),
]
ok = True
for va, vb in cases:
    want = None if vb == 0 else V[names[vb - 1]]
    p = subprocess.run([EXE, F, "V", str(va), str(vb), "131072", "6"],
                       capture_output=True)
    if p.returncode:
        print(f"V{va}->V{vb}: helper failed: {p.stderr.decode().strip()}")
        ok = False; continue
    got, info = p.stdout, p.stderr.decode().strip()
    trigoff = int([t.split("=")[1] for t in info.split() if t.startswith("trigoff=")][0])
    start = d_off + 65536
    bad = 0
    lo, hi = d_off, d_off + d_len
    for i in range(len(got)):
        pos = start + i
        if lo <= pos < hi and i < trigoff:
            exp = raw[pos] if vb == 0 else want[pos - lo]
        else:
            # outside the data, or the trigger read's own fresh bytes:
            # the harness has no asm redirect layer, so those are the
            # plain mix bytes (on deck the versions hook redirects them)
            exp = raw[pos]
        if got[i] != exp:
            bad += 1
    print(f"V{va}->V{vb}: {len(got)} buffered bytes, {bad} wrong; {info}")
    ok &= bad == 0 and "remix=0 " not in info
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
