#!/usr/bin/env python3
"""stems_probe.py -- READ-ONLY: the live-stems engine's counters on the deck
(calls, biggest/last read, files recognised, reads mixed) + the mute mask."""
import struct
import sys
sys.path[:0] = [".", "tools"]
import hid                                          # noqa: E402
from xdj700_usb_shell import PIONEER_VID, open_deck, drain   # noqa: E402
from rgb_overview_apply import br, send             # noqa: E402
import perform_pads as PP                            # noqa: E402
syms = {}
for line in open("build/stems/sm.syms"):
    a, _, n = line.split()
    syms[n.lstrip("_")] = int(a, 16)
c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
try:
    drain(h, 1.0)
    send(h, "$CDJ", 1.5)
    u32 = lambda a: struct.unpack("<I", bytes(br(h, a, 4)))[0]
    print("mask", bytes(br(h, PP.PMASK, 1)).hex(),
          {k: u32(syms["sm_" + k]) for k in ("calls", "hits", "files", "valid", "maxn", "lastn", "lastpos",
                                             "remix", "remixb")},
          "buf", hex(u32(syms["sm_lastbuf"])), "lo/hi",
          hex(u32(syms["sm_buflo"])), hex(u32(syms["sm_bufhi"])),
          "pool", hex(u32(0x08D5FFA8)), "direct", hex(u32(0x08DDFFD4)))
finally:
    h.close()
