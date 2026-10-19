#!/usr/bin/env python3
"""relink_after_move.py -- one-off for decks whose PERFORM pads / MY SETTINGS
rows were installed while hot cues + themes still lived at 0x0FC4xxxx /
0x0FC1xxxx (live data on real decks, 2026-10-12). Those two code pointers
were defused to a zero word (0x081DD7FC); once the hot cue / theme code is
installed at its new home this points them there.

For each pointer: it must currently hold the OLD address or the defused
zero-word address (anything else: refuse); the NEW target's entry word
must be non-zero (= that mod is installed and armed); then one atomic LW
write + read-back. Read-only otherwise. Fresh installs never need this
(new PERFORM pads / MY SETTINGS images already carry the new addresses).
"""
import struct
import sys

sys.path[:0] = [".", "tools"]
DEFUSED = 0x081DD7FC


def literal_addr(img, base, value):
    pat = struct.pack("<I", value)
    offs = [i for i in range(0, len(img) - 3) if img[i:i + 4] == pat]
    assert len(offs) == 1, (hex(value), offs)
    return base + offs[0]


def main():
    import hid
    import perform_pads as PP
    import mysettings as MS
    import theme_switch as TS
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send
    from shasm import assemble
    pads_lit = literal_addr(assemble(PP.HOOK_SRC), PP.PC0, PP.HC_NEXTP)
    ms_lit = literal_addr(MS.image(), MS.MS, TS.TS_BASE)
    jobs = [("hot cue pages", pads_lit, (0x0FC40004, DEFUSED), PP.HC_NEXTP, PP.HC_NEXTP),
            ("theme switching", ms_lit, (0x0FC10000, DEFUSED), TS.TS_BASE, TS.TS_BASE)]
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    if len(c) != 1:
        sys.exit(f"found {len(c)} shell interface(s)")
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    ok = True
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)

        def rd(a):
            return struct.unpack("<I", bytes(br(h, a, 4)))[0]
        for name, lit, olds, new, entry in jobs:
            cur = rd(lit)
            if cur == new:
                print(f"{name}: already linked")
                continue
            if cur not in olds:
                print(f"{name}: pointer {lit:#x} = {cur:#x} unexpected - REFUSED")
                ok = False
                continue
            if rd(entry) == 0:
                print(f"{name}: not installed at {entry:#x} yet - left defused")
                continue
            q = f"{lit:x},{new:x}LW".encode() + b"\r\x00"
            h.write(q + b"\x00" * (26 - len(q)))
            drain(h, 0.5)
            got = rd(lit)
            print(f"{name}: pointer {lit:#x} -> {got:#x}",
                  "PASS" if got == new else "FAIL - power-cycle the deck")
            ok &= got == new
    finally:
        h.close()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
