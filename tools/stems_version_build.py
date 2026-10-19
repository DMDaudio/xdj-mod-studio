#!/usr/bin/env python3
"""stems_version_build.py -- build a STEMS-4 mute version file (WAV/AIFF).

Pipeline (2026-10-10 user-approved design):
  1. Demucs separates the track -> bass/drums/vocals/other WAVs
     (cache/stems_demo/<model>/<track>/).
  2. tools/x7s.py packs the four planes as opus (bass 64k, rest 96k)
     -> one .x7s sidecar (~3.3 MB/min total; 2026-10-10: 21.1 MB for 6.4 min).
  3. THIS tool: decode the planes, align to the mix with
     x7s_to_versions.measure_delay (handles the known opus-64 bass
     pre-roll), then versionfile.build_stems writes the 15 ON-combinations
     as sample-aligned versions inside ONE deck-loadable
     '<track> (STEMS).wav' (mix rides outside as the playable audio).

The deck shows STEMS as 4 mute pads (VOCALS/DRUMS/BASS/OTHER + MIX);
 tapping pad k writes the xver version index mask-1 (see
 versionfile.build_stems docstring order table).

Usage:
  stems_version_build.py AUDIO [-o OUT] [--keep-x7s]
    If cache/stems_demo/.../<AUDIOSTEM>/*.wav exist, reuse them.
"""
import os
import sys
import subprocess

sys.path[:0] = [".", "tools"]


def demucs(audio, out_dir, model="htdemucs"):
    """Separate once; reuse existing stems when present."""
    import re
    stem = os.path.splitext(os.path.basename(audio))[0]
    want = os.path.join(out_dir, model, stem)
    have = all(os.path.exists(os.path.join(want, k + ".wav"))
               for k in ("bass", "drums", "vocals", "other"))
    if have:
        print(f"reusing existing separation: {want}")
        return want
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, "-m", "demucs.separate", "-n", model,
           "-o", out_dir, "-d", "cpu", audio]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return want


def build(audio, out=None):
    import x7s, x7s_to_versions
    best = demucs(audio, "cache/stems_demo")
    S = {k: os.path.join(best, k + ".wav")
         for k in ("bass", "drums", "vocals", "other")}
    for p in S.values():
        if not os.path.exists(p):
            sys.exit(f"missing separation output: {p}")
    xp = os.path.splitext(audio)[0] + ".x7s"
    if not os.path.exists(xp):
        import numpy as np
        srcs = dict(S)
        raw = x7s.build(srcs, codec="opus",
                        bitrates={"bass": 64, "drums": 96, "vocals": 96,
                                  "other": 96})
        open(xp, "wb").write(raw)
        print(f"x7s sidecar: {xp} ({len(raw)/1e6:.1f} MB)")
    else:
        print(f"x7s sidecar exists: {xp}")
    out = out or os.path.splitext(audio)[0] + " (STEMS).wav"
    return x7s_to_versions.build_stems_file(audio, S, out=out)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    audio = sys.argv[1]
    build(audio)


if __name__ == "__main__":
    main()
