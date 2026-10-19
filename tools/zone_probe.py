#!/usr/bin/env python3
"""zone_probe.py DWELL_PRESS POINTS... -- tap-gesture probe with mode readback.

For each (X,Y): performs real TAP gestures (press volley -> release volley,
release flag values from the live hold-vs-release diff), then reads the
screen mode word *0x0B6C4264 + the Y slot. Mode CHANGE = screen layer
switch (e.g. entering perform). No user eyes needed.

One shell open for the whole run. RAM-only, self-clearing. Safe.

Usage:
  .venv/bin/python tools/zone_probe.py 7f00 0a00 7f00 0c00 ...
"""
import re
import sys
import time

import hid

sys.path.insert(0, "tools")
from xdj700_usb_shell import open_deck, drain, PIONEER_VID

PAY_X = (0x0B6C7A34, 0x0B6C7A35)
PAY_Y = (0x0B6C7A46, 0x0B6C7A47)
PAY_P = (0x0B6C7A48, 0x0B6C7A49)
SLOT_Y = (0x0B6C7A58, 0x0B6C7A59)
SLOT_Z = (0x0B6C7A5C, 0x0B6C7A5D)
PRESSURE = 0x0227

DOWN = [   # from live hold-vs-release diff 2026-09-28
    (0x0B6C7A98, 0xF0), (0x0B6C7A9C, 0xFC),
    (0x0B6C7A64, 0xEC), (0x0B6C7A82, 0x80),
]
UP = [     # released-state values (decay steps observed live)
    (0x0B6C7A98, 0xE8), (0x0B6C7A9C, 0xD8),
    (0x0B6C7A64, 0xE0), (0x0B6C7A82, 0x00),
]
MODE = 0x0B6C4264
YSLOT = 0x0B6C7A58


def press_part(x, y):
    p = PRESSURE
    return [
        (PAY_X[0], (x >> 8) & 0xFF), (PAY_X[1], x & 0xFF),
        (PAY_Y[0], (y >> 8) & 0xFF), (PAY_Y[1], y & 0xFF),
        (PAY_P[0], (p >> 8) & 0xFF), (PAY_P[1], p & 0xFF),
        (SLOT_Y[0], (y >> 8) & 0xFF), (SLOT_Y[1], y & 0xFF),
        (SLOT_Z[0], (p >> 8) & 0xFF), (SLOT_Z[1], p & 0xFF),
    ] + DOWN


RELEASE_PART = [
    (PAY_P[0], 0x00), (PAY_P[1], 0x00),
    (SLOT_Z[0], 0x00), (SLOT_Z[1], 0x00),
] + UP


def volley(h, part, secs):
    t0 = time.time()
    n = 0
    while time.time() - t0 < secs:
        for addr, v in part:
            q = f"{addr:x},{v:x}BW\r\x00".encode()
            h.write(q + b"\x00" * (26 - len(q)))
        n += 1
        if n % 25 == 0:
            drain(h, 0.2)
        else:
            time.sleep(0.02)
    return n


def read_mode(h):
    q = f"{MODE:x},3BR\r\x00".encode()
    h.write(q + b"\x00" * (26 - len(q)))
    raw = drain(h, 3.0).decode("ascii", "replace")
    vals = re.findall(r"\b([0-9A-Fa-f]{2})\b(?:\s|$)", raw)
    q2 = f"{YSLOT:x},1BR\r\x00".encode()
    h.write(q2 + b"\x00" * (26 - len(q2)))
    raw2 = drain(h, 3.0).decode("ascii", "replace")
    vals += re.findall(r"\b([0-9A-Fa-f]{2})\b(?:\s|$)", raw2)
    return vals


def main():
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2:
        sys.exit(__doc__)
    pts = [(int(args[i], 16), int(args[i + 1], 16))
           for i in range(0, len(args), 2)]
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        drain(h, 1.5)
        base = read_mode(h)
        print(f"baseline mode+yslot: {' '.join(base)}", flush=True)
        for n, (x, y) in enumerate(pts, 1):
            print(f"[{n}/{len(pts)}] TAPPING X={x:04x} Y={y:04x} "
                  f"(3 press/release cycles)", flush=True)
            for _ in range(3):
                volley(h, press_part(x, y), 1.2)   # press
                volley(h, RELEASE_PART, 0.7)        # release
            vals = read_mode(h)
            print(f"    mode+yslot now: {' '.join(vals)}"
                  f"   {'<<< CHANGED' if vals[:4] != base[:4] else ''}",
                  flush=True)
            time.sleep(0.5)
        print("probe done", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    main()
