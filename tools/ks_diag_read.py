#!/usr/bin/env python3
"""ks_diag_read.py -- read-only probe: one session, says whether the diag
reload ran (status word) and dumps the 22-word ksr_dbg snapshot if it did.
No writes ever. The now-known end-of-session hang wastes no work: reads are
the first thing after the handshake.
"""
import sys

sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from rgb_overview_apply import read_block, send
from keyshift_dsp_apply import syms  # noqa: E402

HOOK = 0x08C92E20


def main():
    s = syms()
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        import struct

        def rd32(a):
            raw = read_block(h, a, 4)
            return raw[0] | raw[1] << 8 | raw[2] << 16 | raw[3] << 24

        status = rd32(s["ksr_status"])
        flag = rd32(s["ksr_flag"])
        hook = rd32(HOOK)
        print(f"status {status:#010x}  flag {flag:#010x}  hook {hook:#010x}")
        if status:
            raw = read_block(h, s["ksr_dbg"], 88)
            dbg = [raw[k] | raw[k + 1] << 8 | raw[k + 2] << 16
                   | raw[k + 3] << 24 for k in range(0, 88, 4)]  # 22 words
            names = ["HPIC pre", "HPIA pre", "ring0", "ring1", "ring2",
                     "ring3", "ring4", "ring5", "ring6", "ring7",
                     "BOOT ret", "HPIC post", "HPIA post", "ring0p",
                     "ring1p", "ring2p", "ring3p", "ring4p", "ring5p",
                     "ring6p", "ring7p", "pins"]
            for n, v in zip(names, dbg[:22]):
                print(f"  dbg {n:10s} {v:#010x}")
        else:
            print("NO reload attempted yet (status 0) - flag is "
                  f"{flag:#x}, installer can arm")
    finally:
        h.close()


if __name__ == "__main__":
    main()
