#!/usr/bin/env python3
"""mod_menu_apply.py [--revert] -- the on-deck MOD MENU (tools/mod_menu.py).

Emulator PASS (mod_menu.py emu: open/close, knob select, WAVEFORM /
VERSION / BEAT JUMP change, panel drawn on the real centre surface).
Needs: selector_apply.py (RELOOP -> free key bit), rgb_overview_apply.py
(mode byte), vredirect code area present (version table; may be empty).

SAFETY GATE: both hooks (pool 0x08DC418C = centre layer release, pool
0x08DC4A50 = centre "skip painter?" check) sit in the centre-waveform
update, which never runs with no track loaded (emu: 0 calls). Code is
written + verified, then the 8 hook bytes are written only when track
length [0x0BA3C050] reads 0 (no track) -> nothing can execute a
half-written pointer.
Chunked (<=120 B/session); rerun until PASS. Reboot = stock.
"""
import struct
import sys

sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from rgb_overview_apply import br, bw, read_block, send, CHUNK_B
from mod_menu import MCAVE, ST, image, hooks

TRACK_LEN = 0x0BA3C050


def rd32(h, a):
    return struct.unpack("<I", bytes(br(h, a, 4)))[0]


def main():
    revert = sys.argv[1:] == ["--revert"]
    img = image()[:ST - MCAVE]          # code + data (state is live)
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
        print(f"track length {tl}, hooks "
              f"{[hex(v) for v in live_hooks]}")
        for (pool, old, new), v in zip(hooks(), live_hooks):
            if v not in (old, new):
                sys.exit(f"FAIL: hook {pool:#x} not stock/ours - aborting")
        if tl != 0:
            sys.exit("REFUSED: a track is loaded. Eject the USB stick first.")
        if revert:
            for pool, old, new in hooks():
                for i, b in enumerate(struct.pack("<I", old)):
                    bw(h, pool + i, b)
            print("RESULT: reverted (no menu)")
            return 0
        live = read_block(h, MCAVE, len(img))
        if live != img:
            bad = [i for i, (a, b) in enumerate(zip(live, img)) if a and a != b]
            if bad:
                sys.exit(f"FAIL: menu area +{bad[0]:#x} holds foreign data")
            todo = [i for i, (a, b) in enumerate(zip(live, img)) if a != b]
            for i in todo[:CHUNK_B]:
                bw(h, MCAVE + i, img[i])
            if len(todo) > CHUNK_B:
                print(f"RESULT: PARTIAL ({len(todo) - CHUNK_B} B left) - "
                      f"run again")
                return 0
            if read_block(h, MCAVE, len(img)) != img:
                sys.exit("FAIL: menu verify")
            print("menu code complete - run again to hook")
            return 0
        for (pool, old, new), v in zip(hooks(), live_hooks):
            if v != new:
                for i, b in enumerate(struct.pack("<I", new)):
                    bw(h, pool + i, b)
                if rd32(h, pool) != new:
                    sys.exit("FAIL: hook verify - REBOOT before loading a "
                             "track")
        print("RESULT: PASS - load a track, press RELOOP/EXIT: menu.")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
