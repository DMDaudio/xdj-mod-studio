#!/usr/bin/env python3
"""zone_sweep.py DWELL_SECS X1 Y1 [X2 Y2 ...] -- ghost-tap a list of points.

For each (X,Y) point: volley the full v3 ghost-tap (payload coords +
mirrored Y/Z assembled slots + DOWN flags) for DWELL_SECS, then move on.
Single shell open for the whole sweep (session budget). Points are hex
BE u16 payload coords, same space as ghosttap.py --xy.

Watch the screen: note WHEN (which point) it reacts. RAM-only writes,
self-clearing (panel refresh overwrites payload; flags decay). Safe.

Usage:
  .venv/bin/python tools/zone_sweep.py 6 7f00 0100 7f00 0800 ...
"""
import sys
import time

import hid

sys.path.insert(0, "tools")
from xdj700_usb_shell import open_deck, drain, PIONEER_VID
from ghosttap import FLAGS  # same four DOWN-flag bytes as v3

PAY_X = (0x0B6C7A34, 0x0B6C7A35)   # payload X hi/lo (PAY+4/5)
PAY_Y = (0x0B6C7A46, 0x0B6C7A47)   # payload Y hi/lo (PAY+22/23)
PAY_P = (0x0B6C7A48, 0x0B6C7A49)   # pressure (PAY+24/25)
SLOT_Y = (0x0B6C7A58, 0x0B6C7A59)  # assembled Y slot
SLOT_Z = (0x0B6C7A5C, 0x0B6C7A5D)  # assembled Z/pressure slot
PRESSURE = 0x0227                   # observed live press value


def volley_part(x, y):
    p = PRESSURE
    return [
        (PAY_X[0], (x >> 8) & 0xFF), (PAY_X[1], x & 0xFF),
        (PAY_Y[0], (y >> 8) & 0xFF), (PAY_Y[1], y & 0xFF),
        (PAY_P[0], (p >> 8) & 0xFF), (PAY_P[1], p & 0xFF),
        (SLOT_Y[0], (y >> 8) & 0xFF), (SLOT_Y[1], y & 0xFF),
        (SLOT_Z[0], (p >> 8) & 0xFF), (SLOT_Z[1], p & 0xFF),
    ] + FLAGS


def main():
    args = sys.argv[1:]
    if len(args) < 3 or (len(args) - 1) % 2:
        sys.exit(__doc__)
    dwell = float(args[0])
    pts = [(int(args[i], 16), int(args[i + 1], 16))
           for i in range(1, len(args), 2)]
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        drain(h, 1.5)
        t0 = time.time()
        for n, (x, y) in enumerate(pts, 1):
            part = volley_part(x, y)
            print(f"[{n}/{len(pts)}] tapping X={x:04x} Y={y:04x} for "
                  f"{dwell:.0f}s (t={time.time() - t0:+.0f}s) -- WATCH",
                  flush=True)
            te = time.time() + dwell
            k = 0
            while time.time() < te:
                for addr, v in part:
                    q = f"{addr:x},{v:x}BW\r\x00".encode()
                    h.write(q + b"\x00" * (26 - len(q)))
                k += 1
                if k % 25 == 0:
                    drain(h, 0.2)
                else:
                    time.sleep(0.02)
        print(f"sweep done ({len(pts)} points) - note WHEN it reacted",
              flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    main()
