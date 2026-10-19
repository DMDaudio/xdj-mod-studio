#!/usr/bin/env python3
"""theme_skips.py -- READ-ONLY: after a MY SETTINGS > [THEME] push on the
deck, list the artwork blocks the theme switcher skipped (its stock-
fingerprint safety check did not match). Emulator baseline: 3 skips, the
play-HISTORY blocks 0x087F8000 / 0x087FA000 / 0x087FC000. Anything else
the deck skips is the "half-themed button" (CLAUDE.md §121)."""
import struct
import sys

sys.path[:0] = [".", "tools"]
import theme_switch as TS


def main():
    log = None
    for line in open("build/themesw/ts.syms"):
        a, _, n = line.split()
        if n == "_ts_skiplog":
            log = int(a, 16)
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
        r32 = lambda a: struct.unpack("<I", bytes(br(h, a, 4)))[0]   # noqa: E731
        n = r32(TS.TS_DATA + 48)
        print(f"skipped blocks: {n} (emulator: 3)")
        for i in range(min(n, 32)):
            print(f"  {r32(log + 4 * i):#010x}")
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
