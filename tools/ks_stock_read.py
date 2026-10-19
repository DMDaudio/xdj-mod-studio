#!/usr/bin/env python3
"""ks_stock_read.py -- read the words keyshift_dsp_apply expects stock on
the DECK (read-only, one session). Prints live vs expected so the constant
mismatch can be pinned."""
import struct
import sys

sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from rgb_overview_apply import br, send

import keyshift_dsp_apply as K

sites = [("POOL_LEN", K.POOL_LEN, 4, K.LEN_STOCK),
         ("POOL_SRC", K.POOL_SRC, 4, K.SRC_STOCK),
         ("CALL_SH", K.CALL_SH, 4, K.CALL_STOCK),
         ("NOP store", K.NOP_SITE, 2, K.NOP_STOCK)]


def dec(raw, n):
    b = bytes(raw)
    if n == 4:
        return struct.unpack("<I", b)[0]
    return struct.unpack("<H", b)[0]


cands = [d for d in hid.enumerate()
         if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
try:
    drain(h, 1.0)
    send(h, "$CDJ", 1.5)
    for name, addr, n, want in sites:
        got = dec(br(h, addr, n), n)
        print(f"{name:9s} @{addr:#x}: live {got:#12x}  want {want:#x}  "
              f"{'OK' if got == want else '<-- MISMATCH'}")
finally:
    h.close()
