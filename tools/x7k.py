#!/usr/bin/env python3
"""x7k.py -- experimental Key Shift container (Phase 1 proof of concept).

Idea under test: pitch-shift WITHOUT the deck's DSP -- preprocess all
keys on the computer into ONE sidecar file, play the chosen key.
This file is the standalone encoder/decoder; no deck, no emulator needed.

Format (.x7k, little-endian):
  magic 4 "X7K1" | ver u16=1 | rate u32 | ch u16 | frames u64 | bpm f32
  keycode u8 (1+2*tonic+minor, 0=unknown) | nver u8 | src_sha256 32 B
  nver x { semis i8 | codec u8 (1=mp3, 2=flac, 3=wav, 4=opus, 5=aac) | off u64 | len u64 }
  ... blobs (each a complete mp3/flac/wav/opus(.ogg)/aac(.adts) bytestring
  of that key version)

Deliberately NO full PCM copy per semitone: versions are stored COMPRESSED
(mp3 default). The proof measures size + tuning accuracy + tempo constancy.

Opus note: libopus resamples to 48 kHz internally (blob is 48 kHz Ogg
Opus even when the header says 96 kHz); decode_pcm() resamples back to
the header rate, so version_pcm() stays sample-comparable. AAC keeps the
source rate natively.

Usage:
  python tools/x7k.py build SRC --semis -4,-3,-2,-1,0,1,2,3,4 [--codec mp3 --bitrate 192] -o OUT.x7k
  python tools/x7k.py info OUT.x7k
  python tools/x7k.py extract OUT.x7k SEMIS OUT.wav
  python tools/x7k.py selftest
"""
import hashlib
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

MAGIC = b"X7K1"
VERSION = 1
CODECS = {"mp3": 1, "flac": 2, "wav": 3, "opus": 4, "aac": 5}
CODEC_NAMES = {v: k for k, v in CODECS.items()}
HDR = "<4sHIIQfBB"          # magic ver rate ch frames bpm keycode nver
HDR_LEN = struct.calcsize(HDR)
ENTRY = "<bBxxQQQ"           # semis codec pad off len frames
ENTRY_LEN = struct.calcsize(ENTRY)


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
    """Decode any audio file to float32 mono/stereo PCM.

    rate/ch are OUTPUT options (resample/remix to the container header);
    they must precede the output file. (Previously they were appended
    after the "-" output and silently ignored -- harmless for same-rate
    mp3/flac, but opus blobs are 48 kHz-internal and decoded short.)
    """
    cmd = ["ffmpeg", "-v", "error", "-i", path]
    if rate:
        cmd += ["-ar", str(rate)]
    if ch:
        cmd += ["-ac", str(ch)]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def peak_freq(x, rate):
    """Dominant frequency of a (mostly) tonal signal via zero-padded FFT."""
    n = 1 << (len(x).bit_length())
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
    return (np.argmax(spec[1:]) + 1) * rate / n


def shift_render(src, semis, td, rate, ch):
    """Rubberband (tempo kept) -> wav path. Returns wav path."""
    out = os.path.join(td, f"k{semis:+d}.wav")
    run(["rubberband", "-q", "-3", "-p", str(semis), src, out])
    return out


def encode_blob(wav, codec, bitrate, td, tag):
    out = os.path.join(td, f"{tag}.{codec}")
    if codec == "mp3":
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "libmp3lame",
             "-b:a", f"{bitrate}k", out])
    elif codec == "flac":
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "flac", out])
    elif codec == "opus":
        # libopus is 48 kHz-internal; resample explicitly so the blob
        # rate is deterministic (decode_pcm resamples back to header).
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-ar", "48000",
             "-c:a", "libopus", "-b:a", f"{bitrate}k", out])
    elif codec == "aac":
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "aac",
             "-b:a", f"{bitrate}k", out])
    else:
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "pcm_s16le",
             out])
    return open(out, "rb").read()


def build(src, semis_list, codec="mp3", bitrate=192, bpm=0.0, keycode=0):
    rate, ch, frames = probe(src)
    sha = hashlib.sha256(open(src, "rb").read()).digest()
    blobs, entries = [], []
    with tempfile.TemporaryDirectory() as td:
        for k in semis_list:
            if k == 0:
                wav = os.path.join(td, "k+0.wav")
                run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ar",
                     str(rate), "-ac", str(ch), "-c:a", "pcm_s16le", wav])
            else:
                wav = shift_render(src, k, td, rate, ch)
            blob = encode_blob(wav, codec, bitrate, td, f"k{k:+d}")
            blobs.append((k, blob))
    off = HDR_LEN + 32 + ENTRY_LEN * len(blobs)
    hdr = struct.pack(HDR, MAGIC, VERSION, rate, ch, frames, float(bpm),
                      keycode & 0xFF, len(blobs))
    body = [hdr, sha]
    for (k, blob), _ in zip(blobs, range(len(blobs))):
        body.append(struct.pack(ENTRY, k, CODECS[codec], off, len(blob),
                                frames))
        off += len(blob)
    for _, blob in blobs:
        body.append(blob)
    return b"".join(body)


def read_index(path):
    b = open(path, "rb").read()
    magic, ver, rate, ch, frames, bpm, keycode, nver = struct.unpack(
        HDR, b[:HDR_LEN])
    if magic != MAGIC:
        raise ValueError("not an .x7k file")
    sha = b[HDR_LEN:HDR_LEN + 32]
    versions = []
    p = HDR_LEN + 32
    for _ in range(nver):
        semis, codec, off, ln, fr = struct.unpack(ENTRY, b[p:p + ENTRY_LEN])
        versions.append({"semis": semis, "codec": CODEC_NAMES[codec],
                         "off": off, "len": ln, "frames": fr})
        p += ENTRY_LEN
    return {"ver": ver, "rate": rate, "ch": ch, "frames": frames,
            "bpm": bpm, "keycode": keycode, "sha": sha.hex(),
            "versions": versions, "blob": b}


def version_blob(idx, semis):
    for v in idx["versions"]:
        if v["semis"] == semis:
            return idx["blob"][v["off"]:v["off"] + v["len"]], v["codec"]
    raise KeyError(f"no {semis:+d} version")


def version_pcm(path, semis, td=None):
    """Decode one key version to float32 PCM (numpy). Returns (pcm, rate)."""
    idx = read_index(path) if isinstance(path, str) else path
    blob, codec = version_blob(idx, semis)
    if td is None:
        import tempfile as _t
        td = _t.mkdtemp()
    src = os.path.join(td, f"v{semis:+d}.{codec}")
    open(src, "wb").write(blob)
    pcm = decode_pcm(src, idx["rate"], idx["ch"])
    return pcm.reshape(-1, idx["ch"]), idx["rate"]


def selftest():
    import math
    print("x7k selftest: synth 440 Hz + real clip, -4..+4")
    td = tempfile.mkdtemp()
    tone = os.path.join(td, "tone.wav")
    rate, dur = 44100, 4.0
    t = np.arange(int(rate * dur)) / rate
    sig = (0.5 * np.sin(2 * math.pi * 440 * t)).astype(np.float32)
    st = np.stack([sig, sig], 1)
    run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate),
         "-ac", "2", "-i", "-", "-c:a", "pcm_s16le", tone], input=st.tobytes())
    semis = list(range(-4, 5))
    fails = 0
    for codec, br in (("mp3", 192), ("mp3", 128), ("opus", 128),
                      ("opus", 96), ("aac", 128), ("flac", 0)):
        data = build(tone, semis, codec=codec, bitrate=br or 192)
        xp = os.path.join(td, f"t.{codec}.x7k")
        open(xp, "wb").write(data)
        idx = read_index(xp)
        sizes = sum(v["len"] for v in idx["versions"])
        ok = True
        for k in semis:
            pcm, _ = version_pcm(idx, k, td)
            f = peak_freq(pcm[:, 0], rate)
            want = 440.0 * 2 ** (k / 12)
            cents = 1200 * math.log2(f / want)
            dur_ok = abs(len(pcm) / rate - dur) < 0.05
            good = abs(cents) < 25 and dur_ok
            ok &= good
            fails += not good
            print(f"  {codec}{br if codec in ('mp3', 'opus', 'aac') else ''} {k:+d}: "
                  f"{f:.1f} Hz (want {want:.1f}, {cents:+.0f} cents) "
                  f"dur {len(pcm)/rate:.2f}s {'OK' if good else 'FAIL'}")
        print(f"  {codec}: {len(data)/1e6:.2f} MB total "
              f"({sizes/len(data)*100:.0f}% audio) "
              f"{'OK' if ok else 'FAIL'}")
    print("RESULT:", "PASS" if fails == 0 else f"FAIL ({fails})")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "selftest":
        sys.exit(selftest())
    if a[0] == "build":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("src")
        ap.add_argument("--semis", default="-4,-3,-2,-1,0,1,2,3,4")
        ap.add_argument("--codec", default="opus",
                          choices=sorted(CODECS))
        ap.add_argument("--bitrate", type=int, default=128)
        ap.add_argument("--bpm", type=float, default=0.0)
        ap.add_argument("-o", required=True)
        r = ap.parse_args(a[1:])
        semis = [int(x) for x in r.semis.split(",")]
        open(r.o, "wb").write(build(r.src, semis, r.codec, r.bitrate,
                                    r.bpm))
        print(f"wrote {r.o} ({os.path.getsize(r.o)/1e6:.1f} MB)")
    elif a[0] == "info":
        idx = read_index(a[1])
        print({k: v for k, v in idx.items() if k not in ("blob", "sha")})
        print("sha:", idx["sha"][:16] + "...")
        for v in idx["versions"]:
            print(f"  {v['semis']:+d} {v['codec']} {v['len']/1e3:.0f} KB")
    elif a[0] == "extract":
        idx = read_index(a[1])
        pcm, rate = version_pcm(idx, int(a[2]), tempfile.mkdtemp())
        run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate),
             "-ac", str(idx["ch"]), "-i", "-", "-c:a", "pcm_s16le", a[3]],
            input=pcm.astype(np.float32).tobytes())
        print(f"extracted {a[2]} -> {a[3]}")
    else:
        sys.exit("usage: build|info|extract|selftest")
