#!/usr/bin/env python3
"""audio_probe.py [ROUNDS] -- READ-ONLY: sample the deck's DSP status shadow.

One shell open. Reads 0x0B6C515C..+0x80 (4 x 32 B windows) ROUNDS times
(default 4, ~40 s per round) while the user plays a track, then reports
which u32 words changed between rounds -- play position / timing
counters tick, flags flip. Writes NOTHING (BR only, RAM window).
Output: docs/deck_audio_probe_<time>.txt
"""
import re
import struct
import sys
import time

sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain

BASE, SIZE = 0x0B6C515C, 0x80


def br(h, addr, n=32):
    q = f"{addr:x},{n - 1:x}BR\r\x00".encode()
    for _ in range(2):
        h.write(q + b"\x00" * (26 - len(q)))
        vals = [int(v, 16) for v in re.findall(
            r"\b([0-9A-Fa-f]{2})\b(?:\s|$)", drain(h, 9.0).decode("ascii", "replace"))]
        if len(vals) >= n:
            return bytes(vals[-n:])
    return None


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    snaps = []
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        drain(h, 1.5)
        for r in range(rounds):
            t = time.time()
            blk = b""
            for off in range(0, SIZE, 32):
                b = br(h, BASE + off)
                blk += b if b else b"\xee" * 32
            snaps.append((t, blk))
            print(f"round {r}: {blk[:16].hex(' ')} ...", flush=True)
    finally:
        h.close()
    lines = []
    for i in range(0, SIZE, 4):
        ws = [struct.unpack_from("<I", s, i)[0] for _, s in snaps]
        if len(set(ws)) > 1:
            dt = snaps[-1][0] - snaps[0][0]
            lines.append(f"+{i:#04x} {BASE + i:#010x}: " +
                         " ".join(f"{w:08x}" for w in ws) +
                         f"   delta/s={(ws[-1] - ws[0]) / dt:,.1f}")
    out = f"docs/deck_audio_probe_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    with open(out, "w") as f:
        for t, s in snaps:
            f.write(f"{t:.1f} {s.hex()}\n")
        f.write("\n".join(lines) + "\n")
    print("changing words:\n" + ("\n".join(lines) or "  (none)"))
    print("saved", out)


if __name__ == "__main__":
    main()
