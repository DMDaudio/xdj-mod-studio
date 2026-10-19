#!/usr/bin/env python3
"""bj_monitor.py [SECS] -- READ-ONLY: watch the PERFORM BEAT JUMP pad state
live while the user taps (latch, swipe flags, page, size rows, track pos).
Prints only changes. latch 51 = pad touched (tap-or-swipe pending); on
release a TAP turns it into the jump code 1..12 (fires the stock jump), a
SWIPE leaves swiped=1 and no jump."""
import struct
import sys
import time

sys.path[:0] = [".", "tools"]
import perform_pads as PP


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    if len(c) != 1:
        sys.exit(f"found {len(c)} shell interface(s)")
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        last = None
        t0 = time.time()
        print("GO - tap the pads now", flush=True)
        while time.time() - t0 < secs:
            st = bytes(br(h, PP.PST, 4))            # latch, page, mode, ..
            sw = bytes(br(h, PP.PSW, 6))            # downx(2) code swiped left
            rows = bytes(br(h, PP.ROWREV & 0x1FFFFFFF, 12))
            rv, fw = struct.unpack_from("<I", rows, 0)[0], struct.unpack_from("<I", rows, 8)[0]
            cur = (st[0], st[1], st[2], sw[2], sw[3], rv, fw)
            if cur != last:
                print(f"{time.time() - t0:6.2f}s latch {st[0]:3d} page {st[1]} mode {st[2]} "
                      f"| pending code {sw[2]:2d} swiped {sw[3]} downx {sw[0] | sw[1] << 8:4d} "
                      f"| rows rev {rv} fwd {fw}", flush=True)
                last = cur
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
