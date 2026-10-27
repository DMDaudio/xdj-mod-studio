#!/usr/bin/env python3
"""master_beat_probe.py -- READ-ONLY: which beat-in-bar byte follows the
MASTER deck? (deck 1 paused, linked deck 2 playing as master). Reads the
candidate bytes found in the emulator (bytes == own beat-in-bar 1..4 while
playing) about 3 times a second for ~8 s."""
import sys
import time
sys.path[:0] = [".", "tools"]
CANDS = [0x09D246B4, 0x0A8D01F6, 0x0B1FDBA0, 0x0B3104B8, 0x0B3104BC,
         0x0B6C512C, 0x0C0E6BA0, 0x0C0E6D3C]


def main():
    import hid
    import fastload
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import send
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        print("t     " + " ".join(f"{a:08x}"[-6:] for a in CANDS))
        t0 = time.time()
        while time.time() - t0 < 8:
            vals = [fastload.BR32(h, a & ~3, 4)[a & 3] for a in CANDS]
            print(f"{time.time() - t0:4.1f}  " + " ".join(f"{v:6d}" for v in vals), flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    main()
