#!/usr/bin/env python3
"""stems4.py -- STEMS-4 files for live stem mute/solo on the XDJ-700.

One ordinary 16-bit stereo WAV/AIFF: the normal MIX (plays and analyses as
a plain track everywhere) + one extra chunk holding 4 sample-aligned
stereo stem planes in the same sample format as the mix, + a fixed 128-B
trailer at the very end of the file:

  +0  "XST4"   +4 data_off (mix sample data, file offset)
  +8  data_len (bytes)   +12 u8 big_endian (AIFF=1)  +13 u8 nplanes (4)
  +16 rel[4]   u32: plane k sample data = file offset data_off + rel[k]
  +32 order    "VDBO" (vocals, drums, bass, other = PERFORM STEMS pads)
  ...zero to 128
WAV chunk `xst4`, AIFF `APPL` + "XST4". On the deck, tools/stems/sm.c
hooks the audio file reads: with any stem muted it reads the 4 planes at
the play position and sums the ones that are ON (clipped), so muting is
sample-accurate and needs no audio-chip work.

  stems4.py build MIX OUT --vocals V --drums D --bass B --other O
  stems4.py selftest
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

sys.path[:0] = [".", "tools"]
from stemfile import decode, probe_rate, fit           # noqa: E402
from versionfile import mix_data_span, write_mix        # noqa: E402

MAGIC = b"XST4"
TRAILER = 128
ORDER = ("vocals", "drums", "bass", "other")          # pad / bit order


def build(mix_src, out, stems, aiff=None, progress=None):
    """stems = {"vocals": path, "drums": path, "bass": path, "other": path}"""
    aiff = out.lower().endswith((".aif", ".aiff")) if aiff is None else aiff
    rate = probe_rate(mix_src)
    if rate not in (44100, 48000):
        conv = os.path.join(tempfile.mkdtemp(), "src48.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mix_src, "-ar", "48000",
                        "-ac", "2", "-c:a", "pcm_s16le", conv], check=True)
        mix_src, rate = conv, 48000
    if progress:
        progress(0.05, "writing the mix")
    write_mix(mix_src, out, rate, aiff)
    d_off, d_len, big = mix_data_span(out)
    frames = d_len // 4
    planes = []
    for i, k in enumerate(ORDER):
        if progress:
            progress(0.1 + 0.6 * i / 4, f"adding the {k} stem")
        a = np.clip(fit(decode(stems[k], rate, 2).astype(np.int32), frames), -32768, 32767)
        planes.append(np.asarray(a, dtype=">i2" if big else "<i2").tobytes())
        assert len(planes[-1]) == d_len
    b = bytearray(open(out, "rb").read())
    head = len(b) + (12 if aiff else 8)                 # chunk header (+OSType)
    pad = (-head) % 4
    first = head + pad
    rel = [first + k * d_len - d_off for k in range(4)]
    body = bytes(pad) + b"".join(planes)
    if aiff:
        b += b"APPL" + struct.pack(">I", 4 + len(body)) + b"XST4" + body
    else:
        b += b"xst4" + struct.pack("<I", len(body)) + body
    if len(body) & 1:
        b += b"\0"
    tb = struct.pack("<4sIIBB2x4I4s", MAGIC, d_off, d_len, int(big), 4, *rel, b"VDBO")
    tb = tb.ljust(TRAILER, b"\0")
    if aiff:
        b += b"APPL" + struct.pack(">I", 4 + TRAILER) + b"XS4Z" + tb
        struct.pack_into(">I", b, 4, len(b) - 8)
    else:
        b += b"xs4t" + struct.pack("<I", TRAILER) + tb
        struct.pack_into("<I", b, 4, len(b) - 8)
    assert bytes(b[-TRAILER:-TRAILER + 4]) == MAGIC
    open(out, "wb").write(b)
    if progress:
        progress(1.0, "stems file written")
    return out


def read(path):
    """-> (d_off, d_len, big, {name: int16 stereo array}, mix array)"""
    b = open(path, "rb").read()
    t = b[-TRAILER:]
    assert t[:4] == MAGIC, "not a STEMS-4 file"
    d_off, d_len, big, n = struct.unpack_from("<IIBB", t, 4)
    rel = struct.unpack_from("<4I", t, 16)
    dt = ">i2" if big else "<i2"
    mix = np.frombuffer(b[d_off:d_off + d_len], dt)
    st = {k: np.frombuffer(b[d_off + rel[i]:d_off + rel[i] + d_len], dt)
          for i, k in enumerate(ORDER)}
    return d_off, d_len, big, st, mix


def expected(st, mask, dt="<i2"):
    """what the deck should play with mute mask (bit i = ORDER[i] muted)"""
    acc = np.zeros(len(next(iter(st.values()))), np.int64)
    for i, k in enumerate(ORDER):
        if not mask & (1 << i):
            acc += st[k].astype(np.int64)
    return np.clip(acc, -32768, 32767).astype(dt)


def selftest():
    td = tempfile.mkdtemp()
    sr = 44100
    t = np.arange(sr * 3) / sr
    paths = {}
    for i, k in enumerate(ORDER):
        x = (0.2 * np.sin(2 * np.pi * (220 * (i + 1)) * t) * 32767).astype(np.int16)
        p = os.path.join(td, k + ".wav")
        import wave
        with wave.open(p, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(np.repeat(x, 2).tobytes())
        paths[k] = p
    mixp = os.path.join(td, "mix.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y"] + sum((["-i", paths[k]] for k in ORDER), [])
                   + ["-filter_complex", "amix=inputs=4:normalize=0", "-c:a", "pcm_s16le", mixp],
                   check=True)
    ok = True
    for ext in ("wav", "aiff"):
        out = os.path.join(td, f"S.{ext}")
        build(mixp, out, paths)
        d_off, d_len, big, st, mix = read(out)
        dt = ">i2" if big else "<i2"
        for k in ORDER:
            ref = fit(decode(paths[k], sr, 2).astype(np.int32), d_len // 4)
            ok &= np.array_equal(st[k].astype(np.int32), np.clip(ref, -32768, 32767).ravel())
        # the mix still decodes as a normal track of the right length
        dec = decode(out, sr, 2)
        ok &= abs(dec.size - d_len // 2) <= 4
        print(f"{ext}: planes exact, plays as a normal {dec.size / 2 / sr:.2f} s track")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "selftest"])
    ap.add_argument("mix", nargs="?")
    ap.add_argument("out", nargs="?")
    for k in ORDER:
        ap.add_argument(f"--{k}")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    build(a.mix, a.out, {k: getattr(a, k) for k in ORDER})
    print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
