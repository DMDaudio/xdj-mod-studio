#!/usr/bin/env python3
"""freeze_probe.py -- READ-ONLY, one session: after a deck freeze, does the
shell still answer, and what state are the PERFORM pads / hooks in?
Prints the PERFORM state block (PST latch/page/mode/drawn), PMASK, zone
state, transport mode, the touch + flush hook pools and the screen mode."""
import struct
import sys
sys.path[:0] = [".", "tools"]
import hid                                         # noqa: E402
from xdj700_usb_shell import PIONEER_VID, open_deck, drain   # noqa: E402
from rgb_overview_apply import br, send            # noqa: E402
import perform_pads as PP                           # noqa: E402

c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
if len(c) != 1:
    sys.exit(f"found {len(c)} shell interface(s) - deck not on USB")
h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
try:
    drain(h, 3.0)
    send(h, "$CDJ", 3.0)
    rd = lambda a, n=4: bytes(br(h, a, n))
    u32 = lambda a: struct.unpack("<I", rd(a))[0]

    def patient(label, fn, tries=4):
        for i in range(tries):
            try:
                v = fn()
                print(label, v, f"(try {i + 1})")
                return v
            except Exception as e:
                print(label, f"timeout (try {i + 1}): {type(e).__name__}")
                drain(h, 3.0)
        return None

    print("shell answers: yes")
    patient("PST  latch/page/mode/-, drawn:",
            lambda: rd(PP.PST, 8).hex(" "))
    patient("PMASK..+4:", lambda: rd(PP.PMASK, 6).hex(" "))
    z = patient("zone state:", lambda: hex(u32(PP.ZSTATE)))
    patient("screen mode:", lambda: hex(u32(0x0BA371D4)))
    patient("transport mode byte:", lambda: rd(0x0B6C4264, 1).hex())
    patient("touch hook [0x08D5278C]:", lambda: hex(u32(0x08D5278C)))
    patient("flush hook [0x08C92E20]:", lambda: hex(u32(0x08C92E20)))
    patient("direct read slot [0x08DDFFD4]:", lambda: hex(u32(0x08DDFFD4)))
    patient("versions hook [0x08D5FFA8]:", lambda: hex(u32(0x08D5FFA8)))
finally:
    h.close()
