#!/usr/bin/env python3
"""x7s_to_versions.py -- Opus v0: .x7s opus sidecar -> deck-loadable file.

The Studio writes small opus stem sidecars (audio + ".x7s"); the deck
plays big version files (mix + `xver` chunk + XVTR trailer). This bridges
them on the Mac, on demand: decode the 4 stem planes, delay-align them to
the mix (all planes share one codec delay; the mix plane measures it via
cross-correlation -- no per-codec constants), then versionfile.build().

Usage:
  .venv/bin/python tools/x7s_to_versions.py [--keys -2,-1,1,2] TRACK...
  .venv/bin/python tools/x7s_to_versions.py --selftest
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, "tools")
import numpy as np

import x7s as X7
import versionfile as VF

STEMS = ("bass", "drums", "vocals", "other")


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def mix_pcm(path):
    """Mix file -> float32 (frames, ch) at its native rate/ch."""
    import json
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels",
         "-of", "json", path], capture_output=True, text=True)
    st = json.loads(probe.stdout)["streams"][0]
    rate, ch = int(st["sample_rate"]), int(st["channels"])
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le",
         "-ar", str(rate), "-ac", str(ch), "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, ch), rate, ch


def measure_delay(mix_src, idx, td):
    """Absolute codec delay (samples) of the x7s blobs vs the mix file.

    All planes share one codec/delay, so align the embedded mix plane to
    the mix file by minimizing the residual over candidate shifts -- the
    same criterion the deck cares about (sample-exact stem sync). No
    per-codec constants, no periodicity traps for xcorr.
    """
    try:
        m_dec, _ = X7.plane_pcm(idx, "mix", td)
    except KeyError:
        print("x7s has no mix plane: delay assumed 0 (stems may flam)")
        return 0
    m_src, rate, ch = mix_pcm(mix_src)
    if rate != idx["rate"] or ch != idx["ch"]:
        print(f"rate/ch mismatch (mix {rate}/{ch} vs x7s {idx['rate']}/{idx['ch']}): "
              f"delay assumed 0")
        return 0
    n = min(240000, len(m_src), len(m_dec))
    a = m_src[:n, 0].astype(np.float64)
    b = m_dec[:n, 0].astype(np.float64)
    a /= max(1e-9, np.max(np.abs(a)))
    b /= max(1e-9, np.max(np.abs(b)))
    best_d, best_e = 0, 1e18
    for d in range(-200, 1201):
        if d >= 0:
            e = np.max(np.abs(a[:n - d] - b[d:n]))
        else:
            e = np.max(np.abs(a[-d:n] - b[:n + d]))
        if e < best_e:
            best_e, best_d = e, d
    print(f"codec delay: {best_d} samples (residual {best_e:.3f} of peak)")
    return best_d


def write_wav(pcm, rate, ch, path):
    pcm = np.asarray(pcm, dtype=np.float32).reshape(-1, ch)
    run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate),
         "-ac", str(ch), "-i", "-", "-c:a", "pcm_s16le", path],
        input=pcm.tobytes())


def build_stems_file(audio, S, out):
    """audio + stems dict (bass/drums/vocals/other wav) -> version file with
    ALL 15 mute-relief combinations (versionfile.build_stems) and the
    opus-64 bass alignment correction (measure_delay)."""
    import versionfile as VF
    xp = audio + ".x7s"
    if not os.path.exists(xp):
        raise FileNotFoundError(f"no sidecar: {xp}")
    idx = X7.read_index(xp)
    have = {v["plane"] for v in idx["versions"]}
    missing = [s for s in STEMS if s not in have]
    if missing:
        raise KeyError(f"x7s missing planes: {missing}")
    _, ext = os.path.splitext(audio)
    aiff = ext.lower() in (".aif", ".aiff")
    with tempfile.TemporaryDirectory() as td:
        delay = measure_delay(audio, idx, td)
        m_src, rate, _ = mix_pcm(audio)
        want = len(m_src)
        wavs = []
        for s in STEMS:
            dec, _ = X7.plane_pcm(idx, s, td)
            if delay > 0:
                dec = dec[delay:]
            elif delay < 0:
                pad0 = np.zeros((-delay, dec.shape[1]), dtype=np.float32)
                dec = np.concatenate([pad0, dec])
            if len(dec) < want:
                pad = np.zeros((want - len(dec), dec.shape[1]), dtype=np.float32)
                dec = np.concatenate([dec, pad])
            else:
                dec = dec[:want]
            p = os.path.join(td, f"{s}.wav")
            write_wav(dec, rate, dec.shape[1], p)
            wavs.append(p)
        names = VF.build_stems(audio, out, stems=dict(zip(STEMS, wavs)),
                               aiff=aiff)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB): {names}")
    return out


def VF_write(pcm, rate, ch, path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar",
                    str(rate), "-ac", str(ch), "-i", "-", "-c:a", "pcm_s16le",
                    path], input=pcm.astype(np.float32).tobytes())


def build_versions(audio, keys=()):
    """audio + '.x7s' -> 'audio (STEMS).wav' with instr/acapella (+keys)."""
    xp = audio + ".x7s"
    if not os.path.exists(xp):
        raise FileNotFoundError(f"no sidecar: {xp}")
    idx = X7.read_index(xp)
    have = {v["plane"] for v in idx["versions"]}
    missing = [s for s in STEMS if s not in have]
    if missing:
        raise KeyError(f"x7s missing planes: {missing}")
    base, ext = os.path.splitext(audio)
    aiff = ext.lower() in (".aif", ".aiff")
    out = f"{base} (STEMS){'.aiff' if aiff else '.wav'}"
    with tempfile.TemporaryDirectory() as td:
        delay = measure_delay(audio, idx, td)
        m_src, rate, _ = mix_pcm(audio)
        want = len(m_src)
        wavs = []
        for s in STEMS:
            dec, _ = X7.plane_pcm(idx, s, td)
            if delay > 0:
                dec = dec[delay:]          # blobs lag the mix: drop pre-roll
            elif delay < 0:
                pad0 = np.zeros((-delay, dec.shape[1]), dtype=np.float32)
                dec = np.concatenate([pad0, dec])  # blobs lead: shift right
            if len(dec) < want:
                pad = np.zeros((want - len(dec), dec.shape[1]), dtype=np.float32)
                dec = np.concatenate([dec, pad])
            else:
                dec = dec[:want]
            p = os.path.join(td, f"{s}.wav")
            write_wav(dec, idx["rate"], idx["ch"], p)
            wavs.append(p)
        names = VF.build(audio, out, stems=wavs, keys=list(keys),
                         aiff=aiff)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB): {names}")
    return out


def selftest():
    print("x7s_to_versions selftest: synth -> x7s (opus) -> version file")
    td = tempfile.mkdtemp()
    srcs = X7._synth_stems(td)
    # exact sample sum as the mix (amix would clip/normalize: useless here)
    acc = None
    for k in ("bass", "drums", "vocals", "other"):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", srcs[k], "-f", "s16le", "-ar",
             "44100", "-ac", "2", "-"], capture_output=True).stdout
        a = np.frombuffer(raw, dtype=np.int16).astype(np.int32)
        acc = a if acc is None else acc + a
    acc = np.clip(acc, -32768, 32767).astype(np.int16)
    mix_src = os.path.join(td, "mix.wav")
    run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "44100",
         "-ac", "2", "-i", "-", "-c:a", "pcm_s16le", mix_src],
        input=acc.tobytes())
    data = X7.build(dict(srcs), codec="opus", bitrate=96)
    audio = os.path.join(td, "song.wav")
    os.rename(mix_src, audio)
    open(audio + ".x7s", "wb").write(data)
    out = build_versions(audio)
    _, _, parts = VF.read_versions(out)
    names = sorted(k for k in parts if k != "mix")
    assert names == ["acapella", "instr"], names
    # alignment: instr + acapella must equal the mix (up to opus noise).
    # A missed codec delay would show up here as thousands of LSBs.
    mix = np.frombuffer(parts["mix"], dtype="<i2").astype(np.int32)
    inst = np.frombuffer(parts["instr"], dtype="<i2").astype(np.int32)
    acap = np.frombuffer(parts["acapella"], dtype="<i2").astype(np.int32)
    err = np.max(np.abs(mix - (inst + acap)))
    print(f"versions {names}; mix vs instr+acapella max err {err} LSB")
    assert err < 4000, err  # opus-96 noise budget (misalignment = ~30000+)
    print("SELFTEST PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        keys, tracks, grab = [], [], False
        for a in sys.argv[1:]:
            if a == "--keys":
                grab = True
            elif grab or a.startswith("--keys="):
                keys = [int(k) for k in a.split("=", 1)[-1].split(",") if k]
                grab = False
            else:
                tracks.append(a)
        if not tracks:
            sys.exit("usage: x7s_to_versions.py [--keys=-2,-1,1,2] TRACK...")
        for t in tracks:
            build_versions(t, keys)
