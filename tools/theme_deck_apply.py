#!/usr/bin/env python3
"""theme_deck_apply.py THEME -- recolour the RUNNING deck (RAM only).

NOT YET RUN ON HARDWARE (emulator PASS: tools/theme_deck.py emu THEME).
Requires the waveform cave + hook (tools/rgb_overview_apply.py) to be
live: the theme pass is triggered from that hook on the next track load.
ONE shell open, fail-stop:
  1. hook pool must already point at the cave (else abort: install RGB).
  2. theme code/data area must be all zero, or hold exactly this theme's
     bytes (resume), or a previous theme's (then it is overwritten).
  3. BW code + data in <=120-byte sessions (resumable; rerun until
     'complete'), BR-verify (fast reads).
  4. LAST: flag byte = 1. Then load any track while NOT playing; the UI
     pauses ~1-2 s once and the theme appears as screens redraw.
Reboot = stock artwork.
"""
import struct
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, "mods")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from rgb_overview_apply import br, bw, read_block, send, CHUNK_B
from rgb_overview import CAVE, HOOK_POOL
from theme_deck import THEME_CODE, THEME_DATA, THEME_FLAG, theme_code, blob


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit("usage: theme_deck_apply.py THEME [--now]")
    name = args[0]
    code, data = theme_code(), blob(name)
    img = bytearray(THEME_DATA - THEME_CODE + len(data))
    img[:len(code)] = code
    img[THEME_DATA - THEME_CODE:] = data
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        hook = struct.unpack("<I", bytes(br(h, HOOK_POOL, 4)))[0]
        if hook != CAVE:
            sys.exit(f"FAIL: hook pool = {hook:#x}; install the waveform cave "
                     f"first (rgb_overview_apply.py) - nothing written")
        live = read_block(h, THEME_CODE, len(img))
        if live != bytes(img):
            # chunked + resumable (long sessions wedge the deck's USB)
            todo = [i for i, (a, b) in enumerate(zip(live, img)) if a != b]
            t = time.time()
            tried = False
            try:
                import fastload
                w0 = todo[0] & ~3
                w1 = (todo[-1] + 4) & ~3
                safe = all(live[j] == 0 or j in todo for j in range(w0, w1))
                if todo and safe:
                    pad = img if w1 <= len(img) else img + bytes(w1 - len(img))
                    fastload.fastblock(h, THEME_CODE + w0, bytes(pad[w0:w1]))
                    if read_block(h, THEME_CODE, len(img)) != bytes(img):
                        sys.exit("FAIL: verify mismatch - nothing armed")
                    print(f"fast path: {w1 - w0} B in {time.time() - t:.0f}s")
                    print("theme data complete - run again to arm")
                    return 0
                raise RuntimeError("partial-word danger")
            except SystemExit:
                raise
            except Exception as e:
                print(f"fast path unavailable ({e}); falling back to BW")
            for i in todo[:CHUNK_B]:
                bw(h, THEME_CODE + i, img[i])
            left = max(0, len(todo) - CHUNK_B)
            print(f"wrote {min(len(todo), CHUNK_B)} bytes in "
                  f"{time.time() - t:.0f}s; {left} left")
            if left:
                print("RESULT: PARTIAL - run again to continue")
                return 0
            if read_block(h, THEME_CODE, len(img)) != bytes(img):
                sys.exit("FAIL: verify mismatch - flag NOT set (no effect)")
            print("theme data complete - run again to arm")
            return 0
        # register the theme with MY SETTINGS: [THEME] row shows OFF, a
        # push there applies it (sets THEME_FLAG). --now = apply at once.
        from mysettings import MSTNAME
        label = name.upper().encode()[:15] + b"\0"
        if bytes(br(h, MSTNAME, len(label))) != label:
            for i, b in enumerate(label):
                bw(h, MSTNAME + i, b)
            if bytes(br(h, MSTNAME, len(label))) != label:
                sys.exit("FAIL: name verify")
        if "--now" in sys.argv:
            bw(h, THEME_FLAG, 1)
            if br(h, THEME_FLAG, 1)[0] != 1:
                sys.exit("FAIL: flag verify")
        print(f"RESULT: PASS - theme '{name}' ready: BROWSE > MENU > "
              f"MY SETTINGS > [THEME] applies it (undo = restart).")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
