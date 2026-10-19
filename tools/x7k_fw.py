#!/usr/bin/env python3
"""x7k_fw.py -- SH firmware support for .x7k key shift files.

This module provides:
1. x7k header parsing (SH-side, runs on the deck)
2. Version table extraction from x7k files
3. Integration with the vredirect hook
4. Version selection via MY SETTINGS

The x7k format (from tools/x7k.py):
  magic 4 "X7K1" | ver u16=1 | rate u32 | ch u16 | frames u64 | bpm f32
  keycode u8 | nver u8 | src_sha256 32 B
  nver x { semis i8 | codec u8 | off u64 | len u64 | frames u64 }
  ... blobs

SH-side: we only need the index (first ~500 bytes) to get the version table.
The blobs stay on the USB stick; the vredirect hook reads them on-demand.

NOTE (verified): the x7k header sits at file offset 0. There is NO
trailer (unlike XVTR version files, which stash 128 B at end-of-file).
Deck code must fseek(0, SET) + fread the head, never tail-read.
Header math: HDR 28 B, nver @25, sha @26..57, entries @58 stride 28.
"""
import struct
import hashlib

MAGIC = b"X7K1"
VERSION = 1

# C-compatible struct for SH firmware
HDR_FMT = "<4sHIIQfBB"  # magic, ver, rate, ch, frames, bpm, keycode, nver
HDR_LEN = struct.calcsize(HDR_FMT)

ENTRY_FMT = "<bBxxQQQ"  # semis, codec, pad, off, len, frames
ENTRY = ENTRY_FMT
ENTRY_LEN = struct.calcsize(ENTRY_FMT)

CODECS = {1: "mp3", 2: "flac", 3: "wav", 4: "opus", 5: "aac"}

def parse_x7k_header(data):
    """Parse x7k header and return dict with index entries."""
    if len(data) < HDR_LEN + 32:
        return None
    magic, ver, rate, ch, frames, bpm, keycode, nver = struct.unpack(
        HDR_FMT, data[:HDR_LEN])
    if magic != MAGIC or ver != VERSION:
        return None
    sha = data[HDR_LEN:HDR_LEN+32]
    entries = []
    p = HDR_LEN + 32
    for _ in range(nver):
        if p + ENTRY_LEN > len(data):
            return None
        semis, codec, off, ln, fr = struct.unpack(ENTRY, data[p:p+ENTRY_LEN])
        entries.append({
            "semis": semis,
            "codec": CODECS.get(codec, "unknown"),
            "off": off,
            "len": ln,
            "frames": fr,
        })
        p += ENTRY_LEN
    return {
        "rate": rate,
        "ch": ch,
        "frames": frames,
        "bpm": bpm,
        "keycode": keycode,
        "sha": sha,
        "versions": entries,
    }

def find_x7k_version_entries(file_handle, file_size):
    """Read just the header/index from an x7k file (first ~1KB)."""
    # Read first 2KB to get header + index
    read_size = min(2048, file_size)
    file_handle.seek(0)
    data = file_handle.read(read_size)
    return parse_x7k_header(data)

def get_version_for_semis(entries, target_semis):
    """Find version entry matching target semitone shift."""
    for e in entries:
        if e["semis"] == target_semis:
            return e
    return None

# SH firmware integration points:
# 1. In file browser: detect .x7k extension, parse header, add version entries to track info
# 2. In track load: if x7k, parse version table, store in track handle
# 3. In MY SETTINGS: add [KEY/STEMS] row to select version
# 4. In vredirect hook: use VSEL to pick version

# vredirect hook data (from tools/vredirect.py):
# VSEL at VDATA+0x00: u8 selected version (0=mix)
# VDATA at 0x081DB500 (survey before deck use)
# VTBL at VDATA+0x08: data_off, data_len, n, rel[8]
# VSEL written by MY SETTINGS hook

# MY SETTINGS hook for version selection:
# Hook at 0x08B8AFE8 (append rows) + 0x08B91828 (push handler)
# New row: [KEY/STEMS] -> versions list

if __name__ == "__main__":
    # Quick test with a built x7k
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as f:
            data = f.read(4096)
        info = parse_x7k_header(data)
        if info:
            print(f"Rate: {info['rate']} Hz, Ch: {info['ch']}, Frames: {info['frames']}")
            print(f"BPM: {info['bpm']:.1f}, Keycode: {info['keycode']}")
            print(f"Versions: {len(info['versions'])}")
            for e in info["versions"]:
                print(f"  {e['semis']:+d} semis, {e['codec']}, "
                      f"off=0x{e['off']:x} len={e['len']} frames={e['frames']}")
        else:
            print("Not a valid x7k file")