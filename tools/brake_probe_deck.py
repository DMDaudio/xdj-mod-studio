#!/usr/bin/env python3
"""brake_probe_deck.py -- READ-ONLY: what the vinyl brake setting looks like
on the deck: the K literal the brake uses (0x08D3714C, stock 6,000,000),
the UTILITY/MY SETTINGS brake index, MODS OFF, UTILITY commit counter."""
import struct
import sys
sys.path[:0] = [".", "tools"]
import mysettings as MS
import utility as U


def main():
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send
    _, us = U.build()
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    r32 = lambda a: struct.unpack("<I", bytes(br(h, a, 4)))[0]
    r8 = lambda a: bytes(br(h, a, 2))[0]
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        k = r32(MS.BRAKE_LIT)
        print(f"brake K literal {k} (stock {MS.BRAKE_STOCK}, x{k / MS.BRAKE_STOCK:.2f})")
        print(f"brake index {r8(MS.MSBRAKE)}, MODS OFF {r8(MS.MSOFF)}, gate {r8(MS.MSGATE)}")
        print(f"UTILITY: commits {r32(us['ut_commits'])}, draws {r32(us['ut_draws'])}, "
              f"pools {[hex(r32(p)) for p in U.POOLS]}, count {r32(U.COUNT)}")
    finally:
        h.close()


if __name__ == "__main__":
    main()
