#!/usr/bin/env python3
"""clock_hunt.py -- READ-ONLY: find RAM words that count while a track plays
(the phase meter's position source froze on silicon). One session: read the
candidate regions twice (DT apart) and list u32 words whose rate looks like
a clock (75/s, 150/s, ms, 44.1k samples, 588-frame blocks ...).
Nothing is written."""
import struct
import sys
import time
sys.path[:0] = [".", "tools"]

REGIONS = [(0x0C0E6000, 0x2000), (0x09D24000, 0x1000), (0x0B6C3000, 0x2000),
           (0x0BA3B000, 0x1000), (0x0BA37000, 0x1000), (0x0C0E4000, 0x2000)]
RATES = {"75/s": 75, "150/s": 150, "ms": 1000, "44.1k": 44100, "blk/s": 75,
         "100/s": 100, "1/60s": 60, "48k": 48000, "x2^16@75": 75 * 65536}


def main():
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import read_block, send
    cands = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        snaps = []
        for k in range(2):
            snap = {}
            for a, n in REGIONS:
                t = time.time()
                snap[a] = (t, bytes(read_block(h, a, n)))
            snaps.append(snap)
            print(f"snapshot {k} done", flush=True)
            if k == 0:
                time.sleep(2)
    finally:
        h.close()
    hits = []
    for a, n in REGIONS:
        (t0, b0), (t1, b1) = snaps[0][a], snaps[1][a]
        dt = t1 - t0
        for o in range(0, n, 4):
            v0, v1 = struct.unpack_from("<I", b0, o)[0], struct.unpack_from("<I", b1, o)[0]
            d = (v1 - v0) & 0xFFFFFFFF
            if d == 0 or d > 0x7FFFFFFF:
                continue
            r = d / dt
            for nm, want in RATES.items():
                if 0.8 * want <= r <= 1.25 * want:
                    hits.append((a + o, v0, v1, r, nm))
    for x in hits:
        print(f"{x[0]:#010x}  {x[1]:>10} -> {x[2]:>10}  {x[3]:9.1f}/s  ~{x[4]}")
    print(f"{len(hits)} candidate(s)")


if __name__ == "__main__":
    main()
