#!/usr/bin/env python3
"""xopus_vfile_test.py -- Ogg Opus through the virtual-WAV layer
(tools/xflac/vfile.c, VF_OPUS) must serve the WAV the deck would get.

Native build (build/xflac/vfile_native, same C as the deck) with stdio
mocks of the deck's file functions. For stereo and mono .opus files made
by ffmpeg/libopus from a real track:
  * header: RIFF/WAVE, 48 kHz, 16-bit, right channel count, data length =
    last granule - pre-skip, LIST INFO with the file's tags
  * whole file read in deck-sized 0x24C0 chunks == the reference decode
    (our own libopus fixed-point build, packet by packet, pre-skip
    dropped), byte for byte
  * 200 random seek+read pairs: a seek restarts the decoder 80 ms early
    (Opus spec pre-roll), so the audio converges but is not bit-identical;
    each slice must match the reference with SNR >= 40 dB and no time
    offset (lag search +-64 samples must pick 0)
  * an Ogg file that is not Opus (Ogg FLAC) is NOT virtualised (passes through untouched)
"""
import os
import random
import struct
import subprocess
import sys
import tempfile

import numpy as np

SRC = os.path.expanduser("~/Downloads/Rekordbox stuff/Contents/Lifelike & "
                         "Roman Salzger/UnknownAlbum/Lifelike & Roman "
                         "Salzger - Radio 84.aiff")
BIN = "build/xflac/vfile_native"
ORACLE = "build/xopus/xopus_native"


def ff(args):
    return subprocess.run(["ffmpeg", "-v", "error", "-y"] + args,
                          capture_output=True, check=True).stdout


def run(path, pairs=()):
    args = [BIN, path] + [str(x) for p in pairs for x in p]
    return subprocess.run(args, capture_output=True, check=True).stdout


def ogg_packets(path):
    """-> (packets, last granule) from an Ogg file (lacing reassembly)"""
    b = open(path, "rb").read()
    o, pk, cur, last = 0, [], b"", 0
    while o + 27 <= len(b):
        assert b[o:o + 4] == b"OggS"
        g = struct.unpack("<q", b[o + 6:o + 14])[0]
        n = b[o + 26]
        lace = b[o + 27:o + 27 + n]
        q = o + 27 + n
        for l in lace:
            cur += b[q:q + l]
            q += l
            if l < 255:
                pk.append(cur)
                cur = b""
        if g != -1:
            last = g
        o = q
    return pk, last


def reference(path, ch):
    pk, last = ogg_packets(path)
    head = pk[0]
    pre = struct.unpack("<H", head[10:12])[0]
    tmp = path + ".pk"
    with open(tmp, "wb") as f:
        for p in pk[2:]:
            f.write(struct.pack("<I", len(p)) + p)
    pcm = subprocess.run([ORACLE, tmp, str(ch)], capture_output=True, check=True).stdout
    pcm = pcm[pre * 2 * ch:]
    return pcm[:(last - pre) * 2 * ch]


def main():
    tmp = tempfile.mkdtemp()
    ok = True
    for name, ch, br in (("stereo", 2, "160k"), ("mono", 1, "96k")):
        f = os.path.join(tmp, name + ".opus")
        ff(["-ss", "60", "-t", "9", "-i", SRC, "-ac", str(ch), "-c:a", "libopus",
            "-b:a", br, "-metadata", "title=Radio 84 (édit)",
            "-metadata", "artist=Lifelike", f])
        ref = reference(f, ch)
        v = run(f)
        riff, size, wave, fmtid, flen, tag, nch, sr, brate, ba, bits = \
            struct.unpack("<4sI4s4sIHHIIHH", v[:36])
        o, dlen, tags = 36, None, {}
        while o + 8 <= len(v):
            cid, cl = v[o:o + 4], struct.unpack("<I", v[o + 4:o + 8])[0]
            if cid == b"data":
                dlen = cl
                break
            if cid == b"LIST":
                q = o + 12
                while q < o + 8 + cl:
                    sid, sl = v[q:q + 4], struct.unpack("<I", v[q + 4:q + 8])[0]
                    tags[sid.decode()] = v[q + 8:q + 8 + sl].rstrip(b"\0").decode()
                    q += 8 + sl + (sl & 1)
            o += 8 + cl + (cl & 1)
        hdr, data = v[:o + 8], v[o + 8:]
        hdr_ok = (riff, wave, fmtid) == (b"RIFF", b"WAVE", b"fmt ") and tag == 1 \
            and nch == ch and sr == 48000 and bits == 16 and dlen == len(ref) \
            and tags.get("INAM", "").startswith("Radio 84") and tags.get("IART") == "Lifelike"
        whole = data == ref
        full = hdr + ref
        rnd = random.Random(11)
        pairs = []
        for _ in range(200):
            fr = rnd.randrange(0, len(ref) // (2 * ch) - 6000)
            pairs.append((len(hdr) + fr * 2 * ch, 4096 * ch))
        got = run(f, pairs)
        r = np.frombuffer(ref, "<i2").astype(float)
        worst, lags = 1e9, set()
        pos = 0
        for off, n in pairs:
            g = np.frombuffer(got[pos:pos + n], "<i2").astype(float)
            pos += n
            s = (off - len(hdr)) // 2
            w = r[s:s + len(g)]
            err = np.sum((g - w) ** 2) + 1e-9
            snr = 10 * np.log10((np.sum(w ** 2) + 1e-9) / err)
            worst = min(worst, snr)
            if snr < 60:                     # check the timing of imperfect slices
                best = max(range(-64, 65), key=lambda L: -np.sum(
                    (g[256:-256] - r[s + L * ch + 256:s + L * ch + len(g) - 256]) ** 2))
                lags.add(best)
        seeks = worst >= 40 and lags <= {0}
        print(f"   tags {tags}")
        print(f"{name}: header {'ok' if hdr_ok else 'BAD'}, whole file "
              f"{'==' if whole else '!='} reference ({len(data)} B), 200 random seeks: "
              f"worst SNR {worst:.1f} dB, lags {sorted(lags) or [0]}")
        ok &= hdr_ok and whole and seeks
    fv = os.path.join(tmp, "v.ogg")
    ff(["-ss", "60", "-t", "2", "-i", SRC, "-vn", "-c:a", "flac", "-f", "ogg", fv])
    passthru = run(fv) == open(fv, "rb").read()
    print(f"Ogg FLAC (not Opus): passed through untouched: {passthru}")
    ok &= passthru
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
