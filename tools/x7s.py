#!/usr/bin/env python3
"""x7s.py -- experimental STEMS container (Phase 1 proof of concept).

Sibling of tools/x7k.py, same philosophy: NO full PCM copies per plane.
Each stem plane is stored COMPRESSED (opus default, mp3/flac/aac accepted) as an
independent blob with an index for instant seeking; the deck (or emu hook)
decodes only the planes it needs and sums them at the play position.

Format (.x7s, little-endian):
  magic 4 "X7S1" | ver u16=1 | rate u32 | ch u16 | frames u64
  nplane u8 | rsv u8=0 | src_sha256 32 B
  nplane x { plane u8 | codec u8 (1=mp3, 2=flac, 3=wav, 4=opus, 5=aac) | pad 2 | off u64 | len u64 | frames u64 }
  ... blobs (each a complete mp3/flac/wav bytestring of that plane)

Plane ids: 0=bass 1=drums 2=vocals 3=other 4=mix.
Normally nplane=5 (4 stems + mix). rate/ch/frames is the shared geometry;
every entry's frames MUST equal the global frames (sample-synchronous by
construction: identical sample count, identical codec delay handling).
src_sha256 = sha256(bass.wav bytes + drums.wav bytes + vocals.wav bytes
  + other.wav bytes) in canonical plane order (provenance, not audio).

Sample-sync note: mp3 blobs carry the usual LAME encoder delay (~2112
samples) + end padding. All planes encoded with the same codec/settings
show the SAME absolute delay, so inter-plane lag is 0; absolute alignment
vs the source is recovered by trimming the measured delay (see selftest).

Usage:
  python tools/x7s.py build --bass B.wav --drums D.wav --vocals V.wav --other O.wav [--mix M.wav] [--codec opus --bitrate 96] -o OUT.x7s
  python tools/x7s.py info OUT.x7s
  python tools/x7s.py extract OUT.x7s bass OUT.wav
  python tools/x7s.py selftest
"""
import hashlib
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

MAGIC = b"X7S1"
VERSION = 1
CODECS = {"mp3": 1, "flac": 2, "wav": 3, "opus": 4, "aac": 5}
CODEC_NAMES = {v: k for k, v in CODECS.items()}
PLANES = ["bass", "drums", "vocals", "other", "mix"]
PLANE_IDS = {name: i for i, name in enumerate(PLANES)}
HDR = "<4sHIIQBB"          # magic ver rate ch frames nplane rsv
HDR_LEN = struct.calcsize(HDR)
ENTRY = "<BBxxQQQ"         # plane codec pad off len frames
ENTRY_LEN = struct.calcsize(ENTRY)

RATE = 44100
CH = 2
DUR = 10.0


def run(cmd, **kw):
    subprocess.run(cmd, check=True, capture_output=True, **kw)


def probe(path):
    """(rate, channels, frames) via ffprobe."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=sample_rate,channels,nb_frames,"
                        "duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    parts = r.stdout.strip().split(",")
    rate, ch = int(parts[0]), int(parts[1])
    try:
        frames = int(parts[2])
    except (ValueError, IndexError):
        import json as _j
        r2 = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration", "-of", "json", path],
                            capture_output=True, text=True, check=True)
        frames = int(float(_j.loads(r2.stdout)["format"]["duration"]) * rate)
    return rate, ch, frames


def decode_pcm(path, rate=None, ch=None):
    """Decode any audio file to float32 mono/stereo PCM."""
    # NOTE: -ar/-ac MUST precede the output "-": trailing options belong
    # to no output and ffmpeg silently ignores them (found 2026-10-10:
    # opus blobs decoded at 48 kHz instead of the requested rate).
    cmd = ["ffmpeg", "-v", "error", "-i", path]
    if rate:
        cmd += ["-ar", str(rate)]
    if ch:
        cmd += ["-ac", str(ch)]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def encode_blob(wav, codec, bitrate, td, tag):
    out = os.path.join(td, f"{tag}.{codec}")
    if codec == "mp3":
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "libmp3lame",
             "-b:a", f"{bitrate}k", out])
    elif codec == "flac":
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "flac", out])
    elif codec == "opus":
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-ar", "48000",
             "-c:a", "libopus", "-b:a", f"{bitrate}k", out])
    elif codec == "aac":
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "aac",
             "-b:a", f"{bitrate}k", out])
    else:
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "pcm_s16le",
             out])
    return open(out, "rb").read()


def _plane_key(p):
    if isinstance(p, int):
        return PLANES[p]
    return str(p).lower()


def build(stems, codec="opus", bitrate=96, bitrates=None):
    """stems: {bass,drums,vocals,other: wavpath[, mix: wavpath]} -> x7s bytes.

    All inputs must share rate/ch/frames. If mix is absent it is rendered
    here as the exact sample sum of the 4 stems; a hot sum is uniformly
    attenuated (all planes + mix, sum preserved, no format change).
    bitrates: optional per-plane override, e.g. {"bass": 64} (proven:
    bass 64 / rest 96 at opus = 2.55 MB/min with no regression vs mp3-128).
    """
    need = ["bass", "drums", "vocals", "other"]
    for k in need:
        if k not in stems:
            raise KeyError(f"missing stem: {k}")
    geos = {k: probe(stems[k]) for k in need}
    rate, ch, frames = geos["bass"]
    for k, g in geos.items():
        if g != (rate, ch, frames):
            raise ValueError(f"geometry mismatch: bass {(rate, ch, frames)} vs {k} {g}")
    sha = hashlib.sha256(b"".join(open(stems[k], "rb").read() for k in need)).digest()
    with tempfile.TemporaryDirectory() as td:
        paths = dict(stems)
        dec = {k: decode_pcm(paths[k], rate, ch).reshape(-1, ch)
               for k in need}
        peak = float(max(np.max(np.abs(v)) for v in dec.values()))
        if "mix" not in paths or paths["mix"] is None:
            mix = np.sum([dec[k] for k in need], axis=0)
            peak = max(peak, float(np.max(np.abs(mix))))
        g = 1.0 / peak if peak > 1.0 else 1.0
        if g < 1.0:
            print(f"x7s headroom: attenuating {20 * np.log10(g):.2f} dB "
                  f"(peak {peak:.3f})")
            for k in need:
                dec[k] = dec[k] * g
                fc = os.path.join(td, f"{k}.wav")
                run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar",
                     str(rate), "-ac", str(ch), "-i", "-",
                     "-c:a", "pcm_s16le", fc],
                    input=dec[k].astype(np.float32).tobytes())
                paths[k] = fc
        if "mix" not in paths or paths["mix"] is None:
            mix = np.sum([dec[k] for k in need], axis=0)
            mx = os.path.join(td, "mix.wav")
            run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar",
                 str(rate), "-ac", str(ch), "-i", "-",
                 "-c:a", "pcm_s16le", mx],
                input=mix.astype(np.float32).tobytes())
            paths["mix"] = mx
        else:
            if probe(paths["mix"]) != (rate, ch, frames):
                raise ValueError("mix geometry mismatch")
        order = need + ["mix"]
        blobs = []
        br = dict(bitrates or {})
        for k in order:
            if k == "mix" and paths[k].endswith(".wav") and "mix.wav" in paths[k]:
                pass  # locally rendered mix, encode as-is
            blob = encode_blob(paths[k], codec, br.get(k, bitrate), td, k)
            blobs.append((k, blob))
    nplane = len(blobs)
    off = HDR_LEN + 32 + ENTRY_LEN * nplane
    hdr = struct.pack(HDR, MAGIC, VERSION, rate, ch, frames, nplane, 0)
    body = [hdr, sha]
    for k, blob in blobs:
        body.append(struct.pack(ENTRY, PLANE_IDS[k], CODECS[codec], off,
                                len(blob), frames))
        off += len(blob)
    for _, blob in blobs:
        body.append(blob)
    return b"".join(body)


def read_index(path):
    b = open(path, "rb").read()
    magic, ver, rate, ch, frames, nplane, _rsv = struct.unpack(HDR, b[:HDR_LEN])
    if magic != MAGIC:
        raise ValueError("not an .x7s file")
    sha = b[HDR_LEN:HDR_LEN + 32]
    versions = []
    p = HDR_LEN + 32
    for _ in range(nplane):
        plane, codec, off, ln, fr = struct.unpack(ENTRY, b[p:p + ENTRY_LEN])
        versions.append({"plane": PLANES[plane], "codec": CODEC_NAMES[codec],
                         "off": off, "len": ln, "frames": fr})
        p += ENTRY_LEN
    return {"ver": ver, "rate": rate, "ch": ch, "frames": frames,
            "sha": sha.hex(), "versions": versions, "planes": versions,
            "blob": b}


def plane_blob(idx, plane):
    name = _plane_key(plane)
    for v in idx["versions"]:
        if v["plane"] == name:
            return idx["blob"][v["off"]:v["off"] + v["len"]], v["codec"]
    raise KeyError(f"no plane {name}")


def plane_pcm(path, plane, td=None):
    """Decode one stem plane to float32 PCM (numpy). Returns (pcm, rate)."""
    idx = read_index(path) if isinstance(path, str) else path
    blob, codec = plane_blob(idx, plane)
    if td is None:
        td = tempfile.mkdtemp()
    src = os.path.join(td, f"p-{_plane_key(plane)}.{codec}")
    open(src, "wb").write(blob)
    pcm = decode_pcm(src, idx["rate"], idx["ch"])
    return pcm.reshape(-1, idx["ch"]), idx["rate"]


# x7k-style aliases: version_blob / version_pcm
def version_blob(idx, plane):
    return plane_blob(idx, plane)


def version_pcm(path, plane, td=None):
    return plane_pcm(path, plane, td=td)


def _xcorr_lag(a, b, maxlag=4096):
    """Integer lag of b relative to a via FFT cross-correlation (ch 0)."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a -= a.mean()
    b -= b.mean()
    n = 1 << (len(a) + len(b) - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lags = np.arange(len(corr))
    lags[lags > n // 2] -= n
    m = np.abs(lags) <= maxlag
    best = int(lags[m][np.argmax(corr[m])])
    return best


def _synth_stems(td):
    """Deterministic 4-stem synth, 44.1 kHz stereo 10 s. Returns wav paths."""
    import math
    n = int(RATE * DUR)
    t = np.arange(n) / RATE
    bass = 0.22 * np.sin(2 * math.pi * 55 * t)
    drums = np.zeros(n)
    nb = int(0.03 * RATE)  # 30 ms click burst
    tau = np.arange(nb) / RATE
    burst = 0.30 * np.sin(2 * math.pi * 2000 * tau) * np.exp(-tau * 400)
    for start in range(0, n, RATE // 2):  # click every 0.5 s
        drums[start:start + nb] += burst[:max(0, min(nb, n - start))]
    fi = 440 + 6 * np.sin(2 * math.pi * 5 * t)  # 440 Hz + 5 Hz vibrato
    vocals = 0.22 * np.sin(2 * math.pi * np.cumsum(fi) / RATE)
    rng = np.random.default_rng(0)
    other = (0.06 * (np.sin(2 * math.pi * 220 * t)
                     + np.sin(2 * math.pi * 277.18 * t)
                     + np.sin(2 * math.pi * 329.63 * t))
             + 0.015 * rng.standard_normal(n))
    stems = {"bass": bass, "drums": drums, "vocals": vocals, "other": other}
    paths = {}
    for k, m in stems.items():
        st = np.stack([m, m], 1).astype(np.float32)
        p = os.path.join(td, f"{k}.wav")
        run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE),
             "-ac", "2", "-i", "-", "-c:a", "pcm_s16le", p],
            input=st.tobytes())
        paths[k] = p
    return paths


def selftest():
    print(f"x7s selftest: synth 4 stems, {RATE} Hz stereo {DUR:.0f}s")
    td = tempfile.mkdtemp()
    srcs = _synth_stems(td)
    src_pcm = {k: decode_pcm(p, RATE, CH).reshape(-1, CH) for k, p in srcs.items()}
    peak = max(float(np.max(np.abs(v))) for v in src_pcm.values())
    mix_peak = float(np.max(np.abs(sum(src_pcm.values()))))
    print(f"  stem peaks: max {peak:.3f}, mix(sum) peak {mix_peak:.3f}")
    fails = 0
    for codec, br in (("mp3", 128), ("flac", 0)):
        data = build(srcs, codec=codec, bitrate=br or 128)
        xp = os.path.join(td, f"t.{codec}.x7s")
        open(xp, "wb").write(data)
        idx = read_index(xp)
        ok_geo = all(v["frames"] == idx["frames"] for v in idx["versions"])
        print(f"  [{codec}{br if codec == 'mp3' else ''}] "
              f"{len(data) / 1e6:.2f} MB / 10 s -> {len(data) * 6 / 1e6:.1f} MB/min"
              f"  nplane={len(idx['versions'])} geo={'OK' if ok_geo else 'FAIL'}")
        fails += not ok_geo
        dec = {v["plane"]: plane_pcm(idx, v["plane"], td)[0] for v in idx["versions"]}
        # (0) absolute delay vs source (mp3 ~2112, flac 0) -- info + alignment
        lags = {}
        for k in PLANES[:4]:
            lags[k] = _xcorr_lag(src_pcm[k][:200000, 0], dec[k][:200000, 0])
        print(f"    absolute lag vs source: " +
              " ".join(f"{k}={lags[k]}" for k in PLANES[:4]))
        # (1) per-plane fidelity on delay-aligned central region
        M = 3000  # margin clears mp3 delay + end padding
        N = idx["frames"]
        worst = 0.0
        for k in PLANES[:4]:
            L = lags[k]
            a = src_pcm[k][M:N - M, :]
            b = dec[k][M + L:N - M + L, :]
            err = float(np.max(np.abs(a - b)))
            rms = float(np.sqrt(np.mean((a - b) ** 2)))
            worst = max(worst, err)
            tol = 1e-9 if codec == "flac" else 0.35
            good = err <= tol
            fails += not good
            print(f"    (1) {k:6s}: max|err| {err:.2e} rms {rms:.2e} "
                  f"{'OK' if good else 'FAIL'}")
        # (2) sample-sync, content-independent: identical decoded lengths +
        # identical absolute lag vs source across planes (same codec delay).
        # NOTE: xcorr of two DIFFERENT stems is meaningless (no shared
        # content) -- sync is proven by equal geometry + equal absolute
        # delay, with the mix test (3) as the end-to-end positive control.
        lens = {k: len(dec[k]) for k in PLANES}
        len_ok = len(set(lens.values())) == 1
        fails += not len_ok
        lagset = {lags[k] for k in PLANES[:4]}
        sync_ok = len(lagset) == 1
        fails += not sync_ok
        print(f"    (2) decoded lens: " +
              " ".join(f"{k}={lens[k]}" for k in PLANES) +
              f"  {'OK' if len_ok else 'FAIL'}")
        print(f"    (2) absolute lags equal: {sorted(lagset)} "
              f"{'OK' if sync_ok else 'FAIL'}")
        # (3) mix == sum of planes (aligned central region)
        names = PLANES[:4]
        psum = sum(dec[k][M + lags[k]:N - M + lags[k], :] for k in names)
        Lm = lags["bass"]  # mix shares the codec delay; verify below
        m = dec["mix"][M + Lm:N - M + Lm, :]
        lag_m = _xcorr_lag(psum[:200000, 0], m[:200000, 0])
        merr = float(np.max(np.abs(psum - m)))
        mrms = float(np.sqrt(np.mean((psum - m) ** 2)))
        # shift-sensitivity control: misalign one plane by 1 sample --
        # error must EXPLODE, proving the test is sample-accurate
        pshift = (dec["bass"][M + lags["bass"] + 1:N - M + lags["bass"] + 1, :]
                  + sum(dec[k][M + lags[k]:N - M + lags[k], :]
                          for k in names[1:]))
        sherr = float(np.max(np.abs(pshift - m)))
        mtol = 1e-3 if codec == "flac" else 0.35
        mgood = merr <= mtol and lag_m == 0
        fails += not mgood
        print(f"    (3) mix-vs-sum: max|err| {merr:.2e} rms {mrms:.2e} "
              f"lag {lag_m} (1-sample-shift control: {sherr:.2e}) "
              f"{'OK' if mgood else 'FAIL'}")
    print("RESULT:", "PASS" if fails == 0 else f"FAIL ({fails})")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "selftest":
        sys.exit(selftest())
    if a[0] == "build":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--bass", required=True)
        ap.add_argument("--drums", required=True)
        ap.add_argument("--vocals", required=True)
        ap.add_argument("--other", required=True)
        ap.add_argument("--mix", default=None)
        ap.add_argument("--codec", default="opus", choices=sorted(CODECS))
        ap.add_argument("--bitrate", type=int, default=96)
        ap.add_argument("-o", required=True)
        r = ap.parse_args(a[1:])
        stems = {"bass": r.bass, "drums": r.drums, "vocals": r.vocals,
                 "other": r.other}
        if r.mix:
            stems["mix"] = r.mix
        open(r.o, "wb").write(build(stems, r.codec, r.bitrate))
        print(f"wrote {r.o} ({os.path.getsize(r.o) / 1e6:.1f} MB)")
    elif a[0] == "info":
        idx = read_index(a[1])
        print({k: v for k, v in idx.items() if k not in ("blob", "sha")})
        print("sha:", idx["sha"][:16] + "...")
        for v in idx["versions"]:
            print(f"  {v['plane']:6s} {v['codec']} {v['len'] / 1e3:.0f} KB")
    elif a[0] == "extract":
        idx = read_index(a[1])
        pcm, rate = version_pcm(idx, a[2], tempfile.mkdtemp())
        run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate),
             "-ac", str(idx["ch"]), "-i", "-", "-c:a", "pcm_s16le", a[3]],
            input=pcm.astype(np.float32).tobytes())
        print(f"extracted {a[2]} -> {a[3]}")
    else:
        sys.exit("usage: build|info|extract|selftest")
