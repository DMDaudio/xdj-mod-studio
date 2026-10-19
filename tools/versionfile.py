#!/usr/bin/env python3
"""versionfile.py -- ONE file, several sample-aligned versions of a track
(stems mixes + key shifts), switchable on the XDJ-700 by read redirection.

The file is an ordinary 16-bit stereo WAV/AIFF (the full mix = version 0,
plays normally on a stock deck and in rekordbox). An extra chunk after
the audio carries the other versions:
  WAV : chunk b"xver"                AIFF: b"APPL" + b"XVER" + body
Body (little-endian header, audio in the FILE's own format/endianness):
  0  4  b"XVER"
  4  2  version = 1
  6  2  n   = number of EXTRA versions (version 0 = the mix, not stored)
  8  4  data_len = length of the mix's sample data in bytes
  12 4  reserved
  16    n x { u32 rel: version start - mix data start (file offsets),
              8 B name (ASCII, NUL-padded) }
  ...   pad to 4, then each version's sample data (data_len bytes each)
TRAILER (last 128 bytes of the file, inside a final `xvtr` / APPL "XVTZ"
chunk): b"XVTR", u32 data_off, u32 data_len, u16 n, u16 0, 8 x u32 rel,
8 x 8 B names (the deck's mod menu shows them). Byte 14 = detected key of
the mix (1 + 2*tonic + minor, 0 = unknown; keydetect.py), bytes 112..119 =
int8 semitone shift per version (0x7F = not a key version) for PERFORM
KEY SHIFT.
Because every version has the mix's exact byte layout, "play version k"
on the deck = add rel_k to the file position of every audio read
(tools/vredirect.py) -- perfect sync, Mac-quality processing.

  versionfile.py build MIX [--stems B D V O] [--keys -2,-1,1,2] -o OUT [--aiff]
      versions (in order): instrumental + acapella (if --stems), then one
      per key shift (rubberband, tempo kept), ordered +1,+2..,-2,-1 so the
      PERFORM [-]/[+] pads step down/up in key from MIX.
  versionfile.py info FILE
  versionfile.py selftest
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

sys.path[:0] = [".", "tools"]
from stemfile import decode, probe_rate, fit

MAGIC = b"XVER"
TRAILER = 128


def mix_data_span(path):
    """(data_offset, data_len, big_endian) of the main sample data."""
    b = open(path, "rb").read()
    big = b[:4] == b"FORM"
    pos = 12
    while pos + 8 <= len(b):
        cid = b[pos:pos + 4]
        n = struct.unpack(">I" if big else "<I", b[pos + 4:pos + 8])[0]
        if not big and cid == b"data":
            return pos + 8, n, False
        if big and cid == b"SSND":
            off = struct.unpack(">I", b[pos + 8:pos + 12])[0]
            return pos + 16 + off, n - 8 - off, True
        pos += 8 + n + (n & 1)
    raise ValueError("no sample data chunk")


def write_mix(src, out, rate, aiff):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:a:0",
           "-map_metadata", "0", "-ac", "2", "-ar", str(rate)]
    cmd += (["-c:a", "pcm_s16be", "-write_id3v2", "1", "-f", "aiff"] if aiff
            else ["-c:a", "pcm_s16le", "-f", "wav"])
    subprocess.run(cmd + [out], check=True)


def keyshift(src, semis, rate, td, progress=None):
    """progress(frac 0..1) follows rubberband's own percent output: pass 1
    (study) is quick, pass 2 (processing) is the real work."""
    out = os.path.join(td, f"k{semis:+d}.wav")
    wav = os.path.join(td, "src.wav")
    if not os.path.exists(wav):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "2",
                        "-ar", str(rate), "-c:a", "pcm_s16le", wav], check=True)
    p = subprocess.Popen(["rubberband", "-3", "-p", str(semis), wav, out],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail, buf, pass2, last = [], b"", False, -1
    while True:
        ch = p.stdout.read(64)
        if not ch:
            break
        buf += ch
        *parts, buf = buf.replace(b"\r", b"\n").split(b"\n")
        for ln in parts:
            t = ln.decode("latin-1").strip()
            if t.startswith("Pass 2"):
                pass2 = True
            if t.endswith("%") and t[:-1].isdigit():
                f = (0.9 * int(t[:-1]) / 100 + 0.1) if pass2 else 0.1 * int(t[:-1]) / 100
                if progress and int(f * 100) != last:
                    last = int(f * 100)
                    progress(f)
            elif t:
                tail = (tail + [t])[-5:]
    if p.wait():
        raise subprocess.CalledProcessError(p.returncode, "rubberband",
                                            "\n".join(tail))
    return out


def build(mix_src, out, stems=None, keys=(), aiff=False, progress=None):
    """progress(frac, note) is optional: fractions are time-weighted (each
    key render ~ the bulk; mix/key detect/stems/writing are small)."""
    # rough cost units: mix 1, key detect 1, stems 1, each key 6, write 1
    units = 3 + (1 if stems else 0) + 6 * len(keys)
    done = [0.0]

    def tick(u, note, inner=0.0):
        if progress:
            progress(min(1.0, (done[0] + inner * u) / units), note)
        if inner >= 1.0:
            done[0] += u
    tick(1, "decoding the mix")
    rate = probe_rate(mix_src)
    if rate not in (44100, 48000):
        # the XDJ-700 plays WAV/AIFF only at 44.1/48 kHz: resample the
        # source first (pitch/duration unchanged), never ship >48 kHz.
        print(f"rate {rate} Hz not deck-playable: converting to 48000 Hz")
        conv = os.path.join(tempfile.mkdtemp(), "src48.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mix_src,
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                        conv], check=True)
        mix_src = conv
        rate = 48000
    write_mix(mix_src, out, rate, aiff)
    tick(1, "detecting the key", 1.0)
    d_off, d_len, big = mix_data_span(out)
    frames = d_len // 4
    names, datas, semis = [], [], []
    try:                                  # key of the original (deck KEY SHIFT)
        import keydetect
        mono = decode(mix_src, rate, 2).astype(np.float64).reshape(-1, 2).mean(1) / 32768
        tonic, minor, _conf = keydetect.detect(mono, rate)
        keycode = 1 + 2 * tonic + int(minor)
    except Exception as e:                # unknown key -> deck shows +-n only
        print("key detection failed:", e)
        keycode = 0
    tick(1, "key detected", 1.0)
    with tempfile.TemporaryDirectory() as td:
        if stems:
            s = [fit(decode(p, rate, 2).astype(np.int32), frames) for p in stems]
            inst = np.clip(s[0] + s[1] + s[3], -32768, 32767)
            datas += [inst, np.clip(s[2], -32768, 32767)]
            names += ["instr", "acapella"]
            semis += [0x7F, 0x7F]          # not a key version
            tick(1, "stems mixed", 1.0)
        for i, k in enumerate(keys):
            note = f"rendering key {k:+d} ({i + 1} of {len(keys)})"
            tick(6, note)
            ks = keyshift(mix_src, k, rate, td,
                          lambda f, note=note: tick(6, note, 0.95 * f))
            datas.append(fit(decode(ks, rate, 2), frames))
            tick(6, note, 1.0)
            names.append(f"key{k:+d}")
            semis.append(k & 0xFF)
    raw = [np.asarray(x, dtype=">i2" if big else "<i2").tobytes() for x in datas]
    for r in raw:
        assert len(r) == d_len, (len(r), d_len)
    tick(1, "writing the version file")
    b = bytearray(open(out, "rb").read())
    hdr_len = 16 + 12 * len(raw)
    pad = (-hdr_len) % 4
    body_start = len(b) + (12 if aiff else 8)      # chunk hdr (+ "XVER" OSType)
    first = body_start + hdr_len + pad
    hdr = struct.pack("<4sHHII", MAGIC, 1, len(raw), d_len, 0)
    for i, nm in enumerate(names):
        rel = first + i * d_len - d_off
        hdr += struct.pack("<I8s", rel, nm.encode()[:8])
    body = hdr + bytes(pad) + b"".join(raw)
    if aiff:
        b += b"APPL" + struct.pack(">I", 4 + len(body)) + b"XVER" + body
        if len(body) & 1:
            b += b"\0"
        struct.pack_into(">I", b, 4, len(b) - 8)
    else:
        b += b"xver" + struct.pack("<I", len(body)) + body
        if len(body) & 1:
            b += b"\0"
        struct.pack_into("<I", b, 4, len(b) - 8)
    # fixed-size TRAILER at the very end: the deck reads the last 128 bytes
    # once per file (fseek -128 from end) instead of walking chunks.
    tb = struct.pack("<4sIIHBB", b"XVTR", d_off, d_len, len(raw), keycode, 0)
    hdr_rel = [struct.unpack_from("<I", hdr, 16 + 12 * k)[0]
               for k in range(len(raw))]
    assert len(raw) <= 8
    tb += b"".join(struct.pack("<I", r) for r in hdr_rel)
    tb += bytes(48 - len(tb))
    tb += b"".join(nm.upper().encode()[:8].ljust(8, b"\0") for nm in names)
    tb += bytes(112 - len(tb))
    tb += bytes(semis).ljust(8, b"\x7f")  # +112: int8 semitones per version
    tb += bytes(TRAILER - len(tb))
    if aiff:
        b += b"APPL" + struct.pack(">I", 4 + TRAILER) + b"XVTZ" + tb
        struct.pack_into(">I", b, 4, len(b) - 8)
    else:
        b += b"xvtr" + struct.pack("<I", TRAILER) + tb
        struct.pack_into("<I", b, 4, len(b) - 8)
    assert bytes(b[-TRAILER:-TRAILER + 4]) == b"XVTR"
    open(out, "wb").write(b)
    tick(1, "done", 1.0)
    return names


def build_stems(mix_src, out, stems, aiff=False):
    """STEMS-4 mute file: stems = {"bass","drums","vocals","other": wav}.

    Emits the 15 ON-combinations (bitmask b = vocals|drums|bass|other,
    version index = b-1; MIX = all on = separate implicit "version 0":
    VTBL n = 16, VSEL wrap in perform_pads handles 0). Order (b, name):
      1 "V"  2 "D"  3 "VD"  4 "B"  5 "VB"  6 "BD"  7 "BDM"
      8 "O"  9 "OV" 10 "OD" 11 "ODV" 12 "OB" 13 "OBV" 14 "OBD"
      15 "OBDV"      (O = other, D = drums, B = bass, V = vocals)
    Each version's audio = the sum of its stems (opus decode noise only).
    All marked semis 0x7F (not key versions).
    """
    rate = probe_rate(mix_src)
    if rate not in (44100, 48000):
        conv = os.path.join(tempfile.mkdtemp(), "src48.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mix_src,
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                        conv], check=True)
        mix_src = conv
        rate = 48000
    write_mix(mix_src, out, rate, aiff)
    tick(1, "detecting the key", 1.0)
    d_off, d_len, big = mix_data_span(out)
    frames = d_len // 4
    order = ["vocals", "drums", "bass", "other"]       # bit 0..3
    tag = {"vocals": "V", "drums": "D", "bass": "B", "other": "O"}
    names, datas, semis = [], [], []
    with tempfile.TemporaryDirectory() as td:
        s = {}
        for k in order:
            s[k] = fit(decode(stems[k], rate, 2).astype(np.int32), frames)
        for mask in range(1, 16):
            acc = np.zeros(fit(s[order[0]], frames).shape, np.int64)
            for bit, k in enumerate(order):
                if mask & (1 << bit):
                    acc += s[k]
            acc = np.clip(acc, -32768, 32767)
            datas.append(acc)
            nm = "".join(tag[k] for bit, k in enumerate(order)
                         if mask & (1 << bit))
            names.append(nm)
            semis.append(0x7F)
        raw = [np.asarray(x, dtype=">i2" if big else "<i2").tobytes()
               for x in datas]
    for r in raw:
        assert len(r) == d_len, (len(r), d_len)
    tick(1, "writing the version file")
    b = bytearray(open(out, "rb").read())
    hdr_len = 16 + 12 * len(raw)
    assert len(raw) <= 8, "XVTR trailer holds 8 names max"
    pad = (-hdr_len) % 4
    body_start = len(b) + (12 if aiff else 8)
    first = body_start + hdr_len + pad
    hdr = struct.pack("<4sHHII", MAGIC, 1, len(raw), d_len, 0)
    for i, nm in enumerate(names):
        rel = first + i * d_len - d_off
        hdr += struct.pack("<I8s", rel, nm.encode()[:8])
    body = hdr + bytes(pad) + b"".join(raw)
    if aiff:
        b += b"APPL" + struct.pack(">I", 4 + len(body)) + b"XVER" + body
        if len(body) & 1:
            b += b"\0"
        struct.pack_into(">I", b, 4, len(b) - 8)
    else:
        b += b"xver" + struct.pack("<I", len(body)) + body
        if len(body) & 1:
            b += b"\0"
        struct.pack_into("<I", b, 4, len(b) - 8)
    tb = struct.pack("<4sIIHBB", b"XVTR", d_off, d_len, len(raw), 0, 0)
    hdr_rel = [struct.unpack_from("<I", hdr, 16 + 12 * k)[0]
               for k in range(len(raw))]
    tb += b"".join(struct.pack("<I", r) for r in hdr_rel)
    tb += bytes(48 - len(tb))
    tb += b"".join(nm.upper().encode()[:8].ljust(8, b"\0") for nm in names)
    tb += bytes(112 - len(tb))
    tb += bytes(semis).ljust(8, b"\x7f")
    tb += bytes(TRAILER - len(tb))
    if aiff:
        b += b"APPL" + struct.pack(">I", 4 + TRAILER) + b"XVTZ" + tb
        struct.pack_into(">I", b, 4, len(b) - 8)
    else:
        b += b"xvtr" + struct.pack("<I", TRAILER) + tb
        struct.pack_into("<I", b, 4, len(b) - 8)
    assert bytes(b[-TRAILER:-TRAILER + 4]) == b"XVTR"
    open(out, "wb").write(b)
    return names


def read_versions(path):
    b = open(path, "rb").read()
    d_off, d_len, big = mix_data_span(path)
    i = b.find(MAGIC, d_off + d_len)
    if b[i + 4:i + 8] == MAGIC:          # AIFF: APPL OSType, then body
        i += 4
    magic, ver, n, dl, _ = struct.unpack_from("<4sHHII", b, i)
    assert magic == MAGIC and dl == d_len
    out = {"mix": b[d_off:d_off + d_len]}
    for k in range(n):
        rel, nm = struct.unpack_from("<I8s", b, i + 16 + 12 * k)
        out[nm.rstrip(b"\0").decode()] = b[d_off + rel:d_off + rel + d_len]
    return d_off, d_len, out


def selftest():
    rate, n = 44100, 44100 * 2
    t = np.arange(n) / rate
    tone = lambda f, a: (a * np.sin(2 * np.pi * f * t) * 32767).astype(np.int16)
    b, d, v, o = tone(55, .3), tone(200, .2), tone(440, .2), tone(3000, .1)
    ok = True
    with tempfile.TemporaryDirectory() as td:
        from stemfile import write_wav
        ps = []
        for nm, x in zip("bdvo", (b, d, v, o)):
            p = os.path.join(td, nm + ".wav"); write_wav(p, np.stack([x, x], 1), rate, b""); ps.append(p)
        mix = np.clip(b.astype(int) + d + v + o, -32768, 32767).astype(np.int16)
        mp = os.path.join(td, "mix.wav"); write_wav(mp, np.stack([mix, mix], 1), rate, b"")
        for aiff in (False, True):
            out = os.path.join(td, "out." + ("aif" if aiff else "wav"))
            names = build(mp, out, stems=ps, keys=(2,), aiff=aiff)
            d_off, d_len, V = read_versions(out)
            back = decode(out, rate, 2)
            plays = len(back) == n and np.array_equal(back[:, 0], mix)
            dt = ">i2" if aiff else "<i2"
            acap = np.frombuffer(V["acapella"], dt).reshape(-1, 2)[:, 0]
            inst = np.frombuffer(V["instr"], dt).reshape(-1, 2)[:, 0]
            exact = np.array_equal(acap, v) and \
                np.array_equal(inst, np.clip(b.astype(int) + d + o, -32768, 32767))
            kp = np.frombuffer(V["key+2"], dt).reshape(-1, 2)[:, 0].astype(float)
            f = np.fft.rfft(kp[rate // 2: rate // 2 + 32768] * np.hanning(32768))
            fr = np.fft.rfftfreq(32768, 1 / rate)
            band = (fr > 400) & (fr < 600)
            peak = fr[band][np.argmax(np.abs(f[band]))]
            print(f"{'AIFF' if aiff else 'WAV '} versions={names} plays-normally={plays} "
                  f"stems-exact={exact} key+2 440Hz->{peak:.1f}Hz (want 493.9)")
            ok &= plays and exact and abs(peak - 493.9) < 6
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("build")
    p.add_argument("mix")
    p.add_argument("--stems", nargs=4)
    p.add_argument("--keys", default="")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--aiff", action="store_true")
    p = sp.add_parser("info")
    p.add_argument("file")
    sp.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "build":
        keys = [int(k) for k in a.keys.split(",") if k]
        # circular order for the PERFORM [-] KEY [+] control (it steps
        # MIX -> 1 .. n -> MIX): ups ascending, then downs ascending, so
        # '+' from MIX = +1, +2 ... and '-' from MIX = -1, -2 ...
        keys = sorted(k for k in keys if k > 0) + sorted(k for k in keys if k < 0)
        print("versions:", build(a.mix, a.out, a.stems, keys, a.aiff))
        return 0
    d_off, d_len, V = read_versions(a.file)
    tr = open(a.file, "rb").read()[-TRAILER:]
    names = [tr[48 + 8 * k:56 + 8 * k].rstrip(b"\0").decode()
             for k in range(struct.unpack_from("<H", tr, 12)[0])]
    print(f"mix data @{d_off} len {d_len}; versions: {list(V)[1:]}; "
          f"trailer {tr[:4]} names {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
