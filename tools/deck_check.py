#!/usr/bin/env python3
"""deck_check.py -- READ-ONLY check before any install (deck_session step 0b).

1. Firmware: a few stretches of the deck's own code must equal the user's
   official v1.15 file (fwhome blk1.bin). Anything else = a different
   firmware on the deck -> stop before touching anything.
2. Leftovers: every pool word our mods hook must still hold its stock value.
   If not, an earlier install (or a failed one) is still in memory -> the
   deck must be switched off and on before installing again.
Writes nothing. Prints RESULT: PASS / STOP lines for deck_session.
"""
import os
import struct
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]
import hid                                             # noqa: E402
from xdj700_usb_shell import PIONEER_VID, open_deck, drain   # noqa: E402
from rgb_overview_apply import br, send                # noqa: E402

BASE = 0x08000000
# deck code never changed by any mod (beat jump handler, KY switch, DSP boot,
# touch hit-tester, MENU dispatcher)
CODE = [0x08D4318E, 0x08B2ABD8, 0x08C3777C, 0x08DD0128, 0x08B9968C]
# pool words the installers re-point (overview, centre palette, PERFORM touch,
# 2DG flush, fread, MY SETTINGS show + push)
POOLS = [0x08DBC5C8, 0x08DC3D94, 0x08D5278C, 0x08C92E20, 0x08D5FFA8,
         0x08B8AFE8, 0x08B91828]

POWER = ("Switch the XDJ-700 OFF and ON, wait for its home screen, then "
         "install again (once).")


def blk1():
    try:
        from emu import fwhome
        p = fwhome.path("blk1.bin")
    except Exception:
        p = None
    for c in (p, "cache/blk1.bin"):
        if c and os.path.exists(c):
            return open(c, "rb").read()
    sys.exit("STOP: your firmware file isn't set up in the app yet "
             "(Home > Firmware: Add).")


def main():
    img = blk1()
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit("STOP: deck not found over USB. Check the cable (USB-B on the "
                 "back), switch the deck on, quit rekordbox / DJ apps.")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        bad = [a for a in CODE
               if bytes(br(h, a, 32)) != img[a - BASE:a - BASE + 32]]
        if bad:
            print(f"    firmware differs at {', '.join(hex(a) for a in bad)}")
            sys.exit("STOP: this deck isn't running the same firmware 1.15 as "
                     "your update file. Check UTILITY on the deck: it must say "
                     "1.15 (update it with Pioneer's official file first). "
                     "Nothing was changed.")
        left = []
        for a in POOLS:
            live = bytes(br(h, a, 4))
            if live != img[a - BASE:a - BASE + 4]:
                left.append(f"{a:#x}={struct.unpack('<I', live)[0]:#x}")
        if left:
            print(f"    already re-pointed: {', '.join(left)}")
            sys.exit("STOP: mods from an earlier install are still in the "
                     "deck's memory. " + POWER)
        print("    RESULT: PASS - firmware 1.15 matches, no earlier mods in memory")
    finally:
        h.close()


if __name__ == "__main__":
    main()
