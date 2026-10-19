#!/usr/bin/env python3
"""touch_hold.py [SECS] -- capture held-vs-released touch state.

READ-ONLY. Start it, then HOLD a screen button ~10s, RELEASE ~10s, done.
Diffing hold vs release snapshots reveals the exact finger-down bytes
for ghost-tap v2. Saves to /tmp/touch_hold_<time>.log
"""
import sys
import time
import hid
sys.path.insert(0, "tools")
from xdj700_usb_shell import open_deck, drain, PIONEER_VID
from touch_probe import snapshot, BASE, SIZE, CHUNK


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        drain(h, 1.5)
        fn = f"/tmp/touch_hold_{int(time.time())}.log"
        f = open(fn, "w")
        print("capturing - HOLD the button now!", flush=True)
        t0 = time.time()
        n = 0
        while time.time() - t0 < secs:
            raws = snapshot(h)
            f.write(f"--- snap {n} t={time.time() - t0:.1f}s ---\n")
            for k, raw in enumerate(raws):
                f.write(f"win{k} @{BASE + k * CHUNK:#x}:\n")
                f.write(raw.decode("ascii", "replace") + "\n")
            f.flush()
            n += 1
        f.close()
        print(f"saved {n} snapshots to {fn}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    main()
