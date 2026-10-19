#!/usr/bin/env python3
"""stemfile.py -- ONE-FILE stems for the XDJ-700 ("stem WAV", format XSTM v1).

The file is an ordinary 16-bit PCM WAV (or AIFF): the full mix lives in
the standard `data` (`SSND`) chunk, so a stock XDJ-700 and rekordbox play
and analyse it like any track. The four stems ride in an extra chunk
AFTER the audio (where ID3 chunks usually sit; RIFF/AIFF readers skip
unknown chunks):

  WAV : chunk id b"xstm"                        (little-endian size)
  AIFF: chunk id b"APPL", OSType b"XSTM" + body (big-endian size)

Chunk body (all little-endian, also inside AIFF):
  0  4  magic  b"XSTM"
  4  2  version = 1
  6  2  nstems  = 4
  8  4  sample_rate          (== the mix)
  12 4  frames               (== the mix's frame count)
  16 2  channels per stem    (1 = mono, 2 = stereo)
  18 2  bits per sample = 16
  20 4  stem order, one ASCII char each: b"bdvo" (bass drums vocals other)
  24 4  flags (0)
  28 4  reserved (0)
  32 .. frame-interleaved int16 LE samples:
        frame k = [s0c0 (s0c1) s1c0 (s1c1) s2c0 .. s3cN]
        -> ONE read at a play position returns all four stems in sync
           (what a deck-side mixer needs; no per-stem seeks).

Commands:
  pack MIX BASS DRUMS VOCALS OTHER -o OUT.wav [--stereo] [--aiff]
       inputs: anything ffmpeg reads (wav/aiff/flac/mp3...). Stems are
       resampled to the mix rate and padded/trimmed to its length.
  info FILE                 print the header
  unpack FILE -o DIR        write the 4 stems back out as WAVs
  selftest                  synthetic round trip (bit-exact) + ffprobe
                            check that the result is a plain playable WAV
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

ORDER = b"bdvo"
NAMES = ["bass", "drums", "vocals", "other"]
HDR = struct.Struct("<4sHHIIHH4sII")


def decode(path, rate=None, channels=2):
    """Any audio file -> int16 array (frames, channels) via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "s16le",
           "-acodec", "pcm_s16le", "-ac", str(channels)]
    if rate:
        cmd += ["-ar", str(rate)]
    cmd += ["-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, "<i2").reshape(-1, channels)


def probe_rate(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                          "-show_entries", "stream=sample_rate", "-of",
                          "csv=p=0", path], check=True, capture_output=True,
                         text=True).stdout.strip()
    # some files (MP3 with cover art, odd containers) print "44100," or
    # several lines -- take the first number
    import re
    m = re.search(r"\d+", out)
    if not m:
        raise ValueError(f"no sample rate for {path}: {out!r}")
    return int(m.group())


def fit(a, frames):
    if len(a) >= frames:
        return a[:frames]
    return np.vstack([a, np.zeros((frames - len(a), a.shape[1]), a.dtype)])


def stem_body(stems, rate, frames, chans):
    hdr = HDR.pack(b"XSTM", 1, 4, rate, frames, chans, 16, ORDER, 0, 0)
    inter = np.concatenate(stems, axis=1)          # (frames, 4*chans)
    return hdr + inter.astype("<i2").tobytes()


def write_wav(out, mix, rate, body):
    data = mix.astype("<i2").tobytes()
    ch = mix.shape[1]
    fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * ch * 2, ch * 2, 16)
    chunks = [b"fmt ", fmt, b"data", data, b"xstm", body]
    payload = b"WAVE"
    for i in range(0, len(chunks), 2):
        cid, c = chunks[i], chunks[i + 1]
        payload += cid + struct.pack("<I", len(c)) + c + (b"\0" if len(c) & 1 else b"")
    with open(out, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(payload)) + payload)


def ext80(rate):
    """IEEE 754 80-bit extended for the AIFF COMM sample rate."""
    e = 16383 + int(np.floor(np.log2(rate)))
    m = int(rate * 2 ** (63 - (e - 16383)))
    return struct.pack(">HQ", e, m)


def write_aiff(out, mix, rate, body):
    ch = mix.shape[1]
    comm = struct.pack(">hIh", ch, len(mix), 16) + ext80(rate)
    ssnd = struct.pack(">II", 0, 0) + mix.astype(">i2").tobytes()
    appl = b"XSTM" + body
    payload = b"AIFF"
    for cid, c in ((b"COMM", comm), (b"SSND", ssnd), (b"APPL", appl)):
        payload += cid + struct.pack(">I", len(c)) + c + (b"\0" if len(c) & 1 else b"")
    with open(out, "wb") as f:
        f.write(b"FORM" + struct.pack(">I", len(payload)) + payload)


def find_body(path):
    """Return (body_offset, body_len) of the XSTM payload, or None."""
    with open(path, "rb") as f:
        head = f.read(12)
        big = head[:4] == b"FORM"
        if head[:4] not in (b"RIFF", b"FORM"):
            return None
        pos = 12
        size = os.path.getsize(path)
        while pos + 8 <= size:
            f.seek(pos)
            cid, n = f.read(4), f.read(4)
            n = struct.unpack(">I" if big else "<I", n)[0]
            if not big and cid == b"xstm":
                return pos + 8, n
            if big and cid == b"APPL":
                if f.read(4) == b"XSTM":
                    return pos + 12, n - 4
            pos += 8 + n + (n & 1)
    return None


def read_stems(path):
    off, n = find_body(path)
    with open(path, "rb") as f:
        f.seek(off)
        body = f.read(n)
    magic, ver, ns, rate, frames, chans, bits, order, _, _ = HDR.unpack(body[:32])
    assert magic == b"XSTM" and ver == 1 and bits == 16
    a = np.frombuffer(body[32:], "<i2").reshape(frames, ns * chans)
    return dict(rate=rate, frames=frames, chans=chans, order=order,
                offset=off), [a[:, i * chans:(i + 1) * chans] for i in range(ns)]


def append_chunk(path, body, aiff):
    """Append the XSTM chunk to an existing WAV/AIFF, fix the outer size."""
    b = bytearray(open(path, "rb").read())
    if aiff:
        assert b[:4] == b"FORM"
        b += b"APPL" + struct.pack(">I", 4 + len(body)) + b"XSTM" + body
        if len(body) & 1:
            b += b"\0"
        struct.pack_into(">I", b, 4, len(b) - 8)
    else:
        assert b[:4] == b"RIFF"
        b += b"xstm" + struct.pack("<I", len(body)) + body
        if len(body) & 1:
            b += b"\0"
        struct.pack_into("<I", b, 4, len(b) - 8)
    open(path, "wb").write(b)


def pack(mix_path, stem_paths, out, stereo=False, aiff=False):
    """The mix file is written by ffmpeg WITH the source's tags (the XDJ
    browser shows artist/time/genre from them -- a tagless WAV shows a
    blank info pane, verified in the emulator), then XSTM is appended."""
    rate = probe_rate(mix_path)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", mix_path, "-map", "0:a:0",
           "-map_metadata", "0", "-ac", "2", "-ar", str(rate)]
    cmd += (["-c:a", "pcm_s16be", "-write_id3v2", "1", "-f", "aiff"] if aiff
            else ["-c:a", "pcm_s16le", "-f", "wav"])
    subprocess.run(cmd + [out], check=True)
    mix = decode(out, rate, 2)
    chans = 2 if stereo else 1
    stems = [fit(decode(p, rate, chans), len(mix)) for p in stem_paths]
    append_chunk(out, stem_body(stems, rate, len(mix), chans), aiff)
    return rate, len(mix), chans


def cmd_info(path):
    h, stems = read_stems(path)
    print(f"{path}: XSTM v1, {h['rate']} Hz, {h['frames']} frames "
          f"({h['frames'] / h['rate']:.1f}s), {h['chans']} ch/stem, order "
          f"{h['order'].decode()}, stem data @ byte {h['offset']}")
    for n, s in zip(NAMES, stems):
        print(f"  {n:7s} peak {np.abs(s.astype(int)).max():6d}")


def selftest():
    rate, n = 44100, 44100 * 3
    t = np.arange(n) / rate
    mk = lambda f, a: (a * np.sin(2 * np.pi * f * t) * 32767).astype("<i2")
    b, d, v, o = mk(55, .3), mk(200, .2), mk(440, .2), mk(3000, .1)
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for nm, x in zip(NAMES, (b, d, v, o)):
            p = os.path.join(td, nm + ".wav")
            write_wav(p, np.stack([x, x], 1), rate, b"")
            paths.append(p)
        mixp = os.path.join(td, "mix.wav")
        mix = (b.astype(int) + d + v + o).clip(-32768, 32767).astype("<i2")
        write_wav(mixp, np.stack([mix, mix], 1), rate, b"")
        ok = True
        for aiff, stereo in ((False, False), (False, True), (True, False)):
            out = os.path.join(td, "out." + ("aif" if aiff else "wav"))
            pack(mixp, paths, out, stereo=stereo, aiff=aiff)
            h, st = read_stems(out)
            exact = all(np.array_equal(s[:, 0], x) for s, x in zip(st, (b, d, v, o)))
            # the file must still decode as a plain 2-ch track of the mix
            back = decode(out, rate, 2)
            plain = len(back) == n and np.array_equal(back[:, 0], mix)
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", out], capture_output=True, text=True).stdout)
            print(f"{'AIFF' if aiff else 'WAV '} {'stereo' if stereo else 'mono  '} "
                  f"stems bit-exact={exact} mix-plays-normally={plain} "
                  f"duration={dur:.3f}s")
            ok &= exact and plain and abs(dur - 3.0) < 0.01
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("pack")
    p.add_argument("mix")
    p.add_argument("stems", nargs=4, metavar="STEM")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--stereo", action="store_true")
    p.add_argument("--aiff", action="store_true")
    p = sp.add_parser("info")
    p.add_argument("file")
    p = sp.add_parser("unpack")
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True)
    sp.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "pack":
        rate, n, ch = pack(a.mix, a.stems, a.out, a.stereo, a.aiff)
        print(f"wrote {a.out}: {rate} Hz, {n / rate:.1f}s, {ch} ch/stem")
        return 0
    if a.cmd == "info":
        cmd_info(a.file)
        return 0
    if a.cmd == "unpack":
        h, stems = read_stems(a.file)
        os.makedirs(a.out, exist_ok=True)
        for nm, s in zip(NAMES, stems):
            write_wav(os.path.join(a.out, nm + ".wav"), s, h["rate"], b"")
        print("wrote", a.out)
        return 0


if __name__ == "__main__":
    sys.exit(main())
