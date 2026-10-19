#!/usr/bin/env python3
"""Ghidra import helper for XDJ-700 MAIN.blk01.bin (offline + in-Ghidra parts).

Offline (no Ghidra needed):
  python3 tools/ghidra_xdj700.py --anchors work/MAIN.blk01.bin
    prints file offsets of UI/button anchors and their load addresses,
    plus candidate literal pools referencing nearby table bases.

In Ghidra (CodeBrowser, SH-4 LE):
  1. New project -> import work/MAIN.blk01.bin as Raw Binary,
     Language: SuperH:SH-4:32:default:LE, Base address 0x08000000,
     block name MAIN.
  2. Also import work/MAIN.blk00.bin at 0xA0000000? No -- blk00 is a
     separate image (version table + code). Import separately if needed.
  3. Run this file as a Ghidra script (it detects the Ghidra runtime and
     defines memory + bookmarks instead of printing).
"""
import struct
import sys

BASE = 0x08000000
ANCHORS = {
    "btn_JogTouch": 0xa5fc1,
    "btn_Cue": 0xa6010,
    "btn_layPause": 0xa5ffb,
    "btn_RecMode": 0xa603c,
    "ui_QUANTIZ": 0x12d8c0,
    "ui_VINYL_BRAKE": 0x12d91d,
    "ui_BROWSE": 0x15afe4,
    "evt_EjectLock": 0x1127da,
}


def offline(path):
    d = open(path, "rb").read()
    print(f"# {path} len {len(d):,}, load base {BASE:#x}")
    print("# anchor file_off load_addr preview")
    for name, off in sorted(ANCHORS.items(), key=lambda kv: kv[1]):
        addr = BASE + off
        print(f"{name:16} {off:#09x} {addr:#010x} {d[off:off+24]!r}")
    print("# pointers into 0x0812xxxx (UI table area), first 20:")
    n = 0
    for i in range(0, len(d) - 4, 2):
        v = struct.unpack_from("<I", d, i)[0]
        if 0x08120000 <= v < 0x08130000:
            print(f"  ref @{i:#09x} (addr {BASE+i:#x}) -> {v:#x}")
            n += 1
            if n >= 20:
                break
    print("# RTS-dense 16KB buckets (>=40): code candidates")
    for k in sorted(_rts_buckets(d)):
        print(f"  code @{k:#09x} ({BASE+k:#x}) count {_rts_buckets(d)[k]}")


def _rts_buckets(d):
    from collections import Counter
    c = Counter()
    for i in range(len(d) - 1):
        if d[i] == 0x0B and d[i + 1] == 0x00:
            c[(i // 0x4000) * 0x4000] += 1
    return {k: v for k, v in c.items() if v >= 40}


try:
    import ghidra  # noqa: F401
    IN_GHIDRA = True
except ImportError:
    IN_GHIDRA = False

if IN_GHIDRA:
    from ghidra.program.model.symbol import SourceType
    fm = currentProgram.getFunctionManager()
    bm = currentProgram.getBookmarkManager()
    for name, off in ANCHORS.items():
        addr = toAddr(BASE + off)
        createLabel(addr, name, True, SourceType.USER_DEFINED)
        bm.setBookmark(addr, "NOTE", "xdj700-anchor", name)
    print("xdj700: anchors labeled")
elif __name__ == "__main__":
    offline(sys.argv[1] if len(sys.argv) > 1 else "work/MAIN.blk01.bin")
