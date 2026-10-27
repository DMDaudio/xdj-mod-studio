#!/usr/bin/env python3
"""fb_meter_probe.py -- READ-ONLY: is the stock phase meter painted straight
into the SCREEN (framebuffers FB0 0x0E780000 / FB1 0x0E83B800) while the UI
layer holds our meter? Reads row y 124 (orange) and 150 (blue), x 258..473,
from the UI layer, FB0 and FB1, five times 0.3 s apart, and prints how many
pixels differ between layer and each framebuffer."""
import struct
import sys
import time
sys.path[:0] = [".", "tools"]

LAYER, FB0, FB1 = 0x09D52B00, 0x0E780000, 0x0E83B800
X0, X1 = 258, 474


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
        row = lambda base, y: struct.unpack(f"<{X1 - X0}H",
                                            fastload.BR32(h, base + 2 * (y * 800 + X0), 2 * (X1 - X0)))
        for k in range(5):
            for y in (124, 150):
                lay, f0, f1 = row(LAYER, y), row(FB0, y), row(FB1, y)
                d0 = sum(a != b for a, b in zip(lay, f0))
                d1 = sum(a != b for a, b in zip(lay, f1))
                print(f"t{k} y{y}: layer vs FB0 {d0:3d} px differ, vs FB1 {d1:3d}  "
                      f"layer[108]={lay[108]:04x} fb0={f0[108]:04x} fb1={f1[108]:04x}")
            time.sleep(0.3)
    finally:
        h.close()


if __name__ == "__main__":
    main()
