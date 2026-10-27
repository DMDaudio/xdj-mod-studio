#!/usr/bin/env python3
"""link_beat_search.py -- READ-ONLY deck search for the MASTER (linked)
deck's beat-in-bar: snapshots of the link-state RAM around 0x0A8D01F6
(this deck's own beat-in-bar there) and 0x0A8BE000.., then lists bytes
whose values stay within 1..4 and take 3+ different values (= counting
beats) while this deck is paused."""
import sys
import time
import numpy as np
sys.path[:0] = [".", "tools"]
REGIONS = [(0x0A8CF000, 0x4000), (0x0A8BE000, 0x2000)]


def main():
    import hid
    import fastload
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import send
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    snaps = {r: [] for r in REGIONS}
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        for k in range(int(sys.argv[1]) if len(sys.argv) > 1 else 6):
            for r in REGIONS:
                snaps[r].append(np.frombuffer(fastload.BR32(h, r[0], r[1]), np.uint8))
            print(f"snapshot {k + 1} at {time.strftime('%H:%M:%S')}", flush=True)
    finally:
        h.close()
    for (base, n), ss in snaps.items():
        a = np.stack(ss)
        inr = ((a >= 1) & (a <= 4)).all(0)
        nd = np.array([len(set(a[:, i])) for i in range(a.shape[1])])
        for i in np.nonzero(inr & (nd >= 3))[0]:
            print(f"  {base + i:#010x}: {list(a[:, i])}")
        changing = np.nonzero(nd >= 3)[0]
        print(f"region {base:#x}: {len(changing)} bytes with 3+ values")


if __name__ == "__main__":
    main()
