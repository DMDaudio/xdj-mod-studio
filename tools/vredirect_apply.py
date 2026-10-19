#!/usr/bin/env python3
"""vredirect_apply.py [--revert] -- version switching (stems / key shift
versions, tools/versionfile.py) on the DECK. Emulator PASS
(tools/vredirect.py: every audio read served from the chosen version).
GATE: hook armed only with NO track loaded (track length [0x0BA3C050]==0)
so no audio read can meet a half-written pointer. Chunked + resumable
(<=120 B per session); rerun until PASS. Reboot = stock.
"""
import struct
import sys
sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from rgb_overview_apply import br, bw, read_block, send, CHUNK_B
from vredirect import HOOK, FREAD, VCAVE, VEND, code

TRACK_LEN = 0x0BA3C050


def rd32(h, a):
    return struct.unpack("<I", bytes(br(h, a, 4)))[0]


def main():
    revert = sys.argv[1:] == ["--revert"]
    img = code()
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        tl, hook = rd32(h, TRACK_LEN), rd32(h, HOOK)
        print(f"track length {tl}, hook {hook:#x}")
        if hook not in (FREAD, VCAVE):
            sys.exit("FAIL: hook site not stock/ours - aborting")
        if tl != 0:
            sys.exit("REFUSED: a track is loaded. Eject the USB stick first.")
        if revert:
            for i, b in enumerate(struct.pack("<I", FREAD)):
                bw(h, HOOK + i, b)
            print("RESULT: reverted (stock reads)")
            return 0
        n = VEND - VCAVE                        # code + data/state area
        want = img + bytes(n - len(img))
        live = read_block(h, VCAVE, n)
        if live[:len(img)] != img:
            if any(a and a != b for a, b in zip(live[:len(img)], img)):
                sys.exit("FAIL: version-cave area holds foreign data")
            if any(live[len(img):]):
                sys.exit("FAIL: version state area not empty")
            import fastload
            left = fastload.install_region(h, VCAVE, img, fastload.SESSION_BUDGET,
                                           cur=live[:len(img)])
            if left:
                print(f"RESULT: PARTIAL ({left} B left) - run again")
                return 0
            if fastload.arm_later():
                print("version code complete - run again to arm")
                return 0
        if hook != VCAVE:
            for i, b in enumerate(struct.pack("<I", VCAVE)):
                bw(h, HOOK + i, b)
            if rd32(h, HOOK) != VCAVE:
                sys.exit("FAIL: hook verify - REBOOT before loading a track")
        print("RESULT: PASS - load a version file; pick the version in "
              "the mod menu (RELOOP/EXIT)")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
