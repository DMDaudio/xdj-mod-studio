#!/usr/bin/env python3
"""popup_diff_deck.py -- ONE open, read the app's popup/USB state block.

The emulator's "USB Error / NO USB" popup is driven by state around
0x0BA12xxx (popup params structs at 0x0BA12ADC/B50/8C8, manager
0x8C2FFB8). The real deck's bytes in the same block, diffed against the
emulator's, expose the word the app reads differently = the popup's
root cause. READ-ONLY. No touching the deck.

Usage: .venv/bin/python tools/popup_diff_deck.py
Saves: /tmp/deck_popup_block.bin (1 KB @ 0x0BA12800)
"""
import re
import sys
import time

import hid

sys.path.insert(0, "tools")
from xdj700_usb_shell import PIONEER_VID, open_deck, drain

BASE = 0x0BA12800
SIZE = 0x400                      # 1 KB
CHUNK = 32                        # BR count is 5-bit masked (max 32)


def parse(raw):
    return [int(v, 16) for v in
            re.findall(r"\b([0-9A-Fa-f]{2})\b(?:\s|$)", raw)]


def main():
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    out = bytearray()
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        print("handshake:", drain(h, 1.5)[:16], flush=True)
        for off in range(0, SIZE, CHUNK):
            addr = BASE + off
            q = f"{addr:x},{CHUNK - 1:x}BR\r\x00".encode()
            h.write(q + b"\x00" * (26 - len(q)))
            vals = parse(drain(h, 9.0).decode("ascii", "replace"))
            if len(vals) < CHUNK:
                print(f"  @{addr:08x}: SHORT ({len(vals)}/{CHUNK}) -- "
                      f"retry window once", flush=True)
                h.write(q + b"\x00" * (26 - len(q)))
                vals = parse(drain(h, 9.0).decode("ascii", "replace"))
            out.extend(vals[:CHUNK])
            print(f"  @{addr:08x}: {len(vals)}/{CHUNK} "
                  f"({' '.join(f'{v:02x}' for v in vals[:8])}...)",
                  flush=True)
            time.sleep(0.2)
    finally:
        h.close()
    open("/tmp/deck_popup_block.bin", "wb").write(bytes(out))
    print(f"saved {len(out)} bytes -> /tmp/deck_popup_block.bin")


if __name__ == "__main__":
    main()
