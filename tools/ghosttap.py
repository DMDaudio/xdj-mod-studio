#!/usr/bin/env python3
"""ghosttap.py [SECS] -- synthesize a touchscreen press over USB (v4).

v4 model (2026-10-02, live-verified): the PANL PAYLOAD itself is the
live touch channel. PAY+21 is the touch-down marker (1=finger down),
PAY+22/23 = Y (BE u16, 0=TOP of screen, bottom row ~0x39D), PAY+24/25 =
pressure (BE u16; light ~0x5A, normal pad press ~0x22B..0x23B).
PAY+4/5 is the TEMPO FADER readback -- NOT touch X; v1-v3 wrongly wrote
it (harmless: panel refresh rewrites it). Touch X field: NOT in
PAY+16..31 (verified); head PAY+0..15 still unproven -- until found,
only Y steers the tap vertically.

Plus the four DOWN-flag bytes outside the refresh range (v2-proven).
Watch the screen + listen.

RAM-only BW writes; self-clearing. Safe.

Usage: .venv/bin/python tools/ghosttap.py [30] [--y 039d] [--press 02f2]
"""
import sys
import time
import hid
sys.path.insert(0, "tools")
from xdj700_usb_shell import open_deck, drain, PIONEER_VID

# Vinyl-Break: v2 live-proven pair (bottom button row).
DEF_Y, DEF_P = 0x039D, 0x02F2
DOWN = (0x0B6C7A45, 0x01)          # PAY+21 touch-down marker
Y_ADDR = (0x0B6C7A46, 0x0B6C7A47)  # PAY+22/23, BE u16
P_ADDR = (0x0B6C7A48, 0x0B6C7A49)  # PAY+24/25, BE u16
FLAGS = [
    (0x0B6C7A98, 0xF0),  # struct+0x70 touch flag (down=f0)
    (0x0B6C7A9C, 0xFC),  # struct+0x74 touch flag (down=fc)
    (0x0B6C7A64, 0xEC),  # struct+0x3C touch flag (down=ec)
    (0x0B6C7A82, 0x80),  # struct+0x5A touch flag (down=80)
]
# known zones (2026-10-02 capture): PERFORM Y=0x0082 P=0x005A,
# pad A Y=0x0312 P=0x022B, pad B Y=0x029F P=0x023B, VINYL BRAKE 0x039D.


def press_part(y, p):
    return [(DOWN[0], DOWN[1]),
            (Y_ADDR[0], (y >> 8) & 0xFF), (Y_ADDR[1], y & 0xFF),
            (P_ADDR[0], (p >> 8) & 0xFF), (P_ADDR[1], p & 0xFF)] + FLAGS


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("secs", nargs="?", type=int, default=30)
    ap.add_argument("--y", default=None, metavar="YYYY",
                    help="hex BE u16 touch Y, 0=top (default VB 039d)")
    ap.add_argument("--press", default=None, metavar="PP",
                    help="hex BE u16 pressure (default 02f2)")
    ap.add_argument("--tap", action="store_true",
                    help="press/release cycles (~1.2s down, 0.7s up) "
                         "instead of one long hold")
    ap.add_argument("--flags-only", action="store_true",
                    help="DOWN flags only (diagnostic)")
    a = ap.parse_args()
    y = int(a.y, 16) if a.y else DEF_Y
    p = int(a.press, 16) if a.press else DEF_P
    part = FLAGS if a.flags_only else press_part(y, p)
    release = [(DOWN[0], 0x00),
               (P_ADDR[0], 0x00), (P_ADDR[1], 0x01)]
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)

    def volley(bytes_part, secs):
        t0 = time.time()
        n = 0
        while time.time() - t0 < secs:
            for addr, v in bytes_part:
                q = f"{addr:x},{v:x}BW\r\x00".encode()
                h.write(q + b"\x00" * (26 - len(q)))
            n += 1
            if n % 25 == 0:
                drain(h, 0.2)
            else:
                time.sleep(0.02)
        return n

    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        drain(h, 1.5)
        print(f"ghost-tapping Y={y:04x} P={p:04x} for {a.secs}s "
              f"-- WATCH THE SCREEN", flush=True)
        print("writing:", ", ".join(f"{ad:#x}={v:#x}" for ad, v in part),
              flush=True)
        if a.tap:
            left = a.secs
            while left > 0:
                volley(part, min(1.2, left))
                left -= 1.2
                if left > 0:
                    volley(release, min(0.7, left))
                    left -= 0.7
        else:
            volley(part, a.secs)
        volley(release, 0.3)
        print("done - did anything react?", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    main()
