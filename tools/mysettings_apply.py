#!/usr/bin/env python3
"""mysettings_apply.py [--revert] -- MY SETTINGS rows on the DECK
([MODS] [WAVEFORM] [BEAT JUMP] [KEY/STEMS] in BROWSE > MENU > MY SETTINGS;
tools/mysettings.py, emulator PASS).

Hooks (pool words, written LAST): 0x08B8AFE8 (show MY SETTINGS list) and
0x08B91828 (browse popup push). Both only run while you are browsing with
the MENU popup / pushing the knob in browse -> GATE: no track loaded
(stick out, so browse is empty) and don't touch the deck during install.
Needs rgb_overview_apply.py (mode byte) and vredirect code area.
Chunked (<=120 B/session); rerun until PASS. Reboot = stock.
"""
import struct
import sys

sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from rgb_overview_apply import br, bw, read_block, send, CHUNK_B
from mysettings import MS, image, hooks

TRACK_LEN = 0x0BA3C050


def rd32(h, a):
    return struct.unpack("<I", bytes(br(h, a, 4)))[0]


def main():
    revert = sys.argv[1:] == ["--revert"]
    img = image()
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        tl = rd32(h, TRACK_LEN)
        live_hooks = [rd32(h, pool) for pool, _, _ in hooks()]
        print(f"track length {tl}, hooks {[hex(v) for v in live_hooks]}")
        for (pool, old, new), v in zip(hooks(), live_hooks):
            if v not in (old, new):
                sys.exit(f"FAIL: hook {pool:#x} not stock/ours - aborting")
        if tl != 0:
            sys.exit("REFUSED: a track is loaded. Eject the USB stick first.")
        if revert:
            for pool, old, new in hooks():
                for i, b in enumerate(struct.pack("<I", old)):
                    bw(h, pool + i, b)
            print("RESULT: reverted (stock MY SETTINGS)")
            return 0
        live = read_block(h, MS, len(img))
        if live != img:
            bad = [i for i, (a, b) in enumerate(zip(live, img)) if a and a != b]
            if bad:
                sys.exit(f"FAIL: area +{bad[0]:#x} holds foreign data")
            import fastload
            left = fastload.install_region(h, MS, img, fastload.SESSION_BUDGET,
                                           cur=live)
            if left:
                print(f"RESULT: PARTIAL ({left} B left) - run again")
                return 0
            if fastload.arm_later():
                print("code complete - run again to hook")
                return 0
        for (pool, old, new), v in zip(hooks(), live_hooks):
            if v != new:
                for i, b in enumerate(struct.pack("<I", new)):
                    bw(h, pool + i, b)
                if rd32(h, pool) != new:
                    sys.exit("FAIL: hook verify - REBOOT before browsing")
        print("RESULT: PASS - BROWSE > MENU: MY SETTINGS has the mod rows.")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
