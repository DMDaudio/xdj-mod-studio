#!/usr/bin/env python3
"""utility_apply.py [--revert] -- the mod rows in the deck's UTILITY screen
(MODS, WAVEFORM COLOR, VINYL BRAKE LENGTH, HOT CUE GATE, THEME), on the
DECK. RAM only, reboot = stock. Emulator PASS: tools/utility.py emu
(with and without theme switching). NOT YET RUN ON HARDWARE.

Keep UTILITY closed while installing. Needs the MY SETTINGS rows (5c):
same state bytes.

  (no flag)  reads the 16 stock UTILITY records off the deck (checked:
             labels, callbacks, nobody editing), writes the module with
             that copy + our 5 records (fastload, foreign-data check),
             then arms in a fresh short session: LW self-test, the four
             table pools -> the copy (each verified), THEN the row count
             16 -> 21.
  --revert   count back to 16 first, then the pools (each verified).
"""
import os
import struct
import sys

sys.path[:0] = [".", "tools"]
import utility as U          # noqa: E402

CB_LO, CB_HI = 0x08DD0000, 0x08DD3000
FIRST_LABEL = 0xA80D486C     # "PLAY MODE"


def check_stock(tab):
    for i in range(U.STOCK_N):
        r = struct.unpack_from("<13I", tab, i * U.REC)
        if (r[0] >> 24) != 0xA8 or r[4] != 0xFFFFFFFF:
            return f"record {i}: label {r[0]:#x} / editing {r[4]:#x}"
        if not all(CB_LO <= x < CB_HI for x in r[6:]):
            return f"record {i}: callbacks {[hex(x) for x in r[6:]]}"
    if struct.unpack_from("<I", tab, 0)[0] != FIRST_LABEL:
        return "first row is not PLAY MODE"
    return None


def main():
    revert = "--revert" in sys.argv
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, read_block, send

    def rd32(h, a):
        return struct.unpack("<I", bytes(br(h, a, 4)))[0]

    def lw(h, addr, val):
        q = f"{addr:x},{val:x}LW".encode() + b"\r\x00"
        h.write(q + b"\x00" * (26 - len(q)))
        drain(h, 0.5)

    _, s = U.build()
    tab_ours = s["ut_tab"]
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        pools = [rd32(h, p) for p in U.POOLS]
        count = rd32(h, U.COUNT)
        print(f"UTILITY pools {[hex(p) for p in pools]}, count {count}")
        if revert:
            if all(p == U.STOCK_TAB for p in pools) and count == U.STOCK_N:
                print("not armed - nothing to do")
                return 0
            lw(h, U.COUNT, U.STOCK_N)
            if rd32(h, U.COUNT) != U.STOCK_N:
                sys.exit("FAIL: count not restored - REBOOT the deck")
            for p in U.POOLS:
                lw(h, p, U.STOCK_TAB)
                if rd32(h, p) != U.STOCK_TAB:
                    sys.exit(f"FAIL: pool {p:#x} not restored - REBOOT the deck")
            print("RESULT: reverted")
            return 0
        if all(p == tab_ours for p in pools) and count == U.STOCK_N + U.NOURS:
            print("RESULT: PASS (already armed)")
            return 0
        if any(p not in (U.STOCK_TAB, tab_ours) for p in pools) or \
                count not in (U.STOCK_N, U.STOCK_N + U.NOURS):
            sys.exit("FAIL: UTILITY table pools/count not stock - nothing written")
        if count != U.STOCK_N:
            sys.exit("FAIL: count already raised with stock pools - REBOOT the deck")
        stock = bytes(read_block(h, U.STOCK_TAB, U.STOCK_N * U.REC))
        bad = check_stock(stock)
        if bad:
            sys.exit(f"FAIL: stock UTILITY table unexpected ({bad}) - close "
                     "UTILITY and try again; nothing written")
        base, img, s = U.image(stock)
        want = bytearray(img) + bytes(max(0, s["end"] - base - len(img)))
        import fastload
        left = fastload.install_region(h, base, bytes(want), fastload.SESSION_BUDGET)
        if left:
            print(f"RESULT: PARTIAL ({left} B left) - run again")
            return 0
        if fastload.arm_later():
            print("code complete - run again to arm (fresh short session)")
            return 0
        scratch = s["ut_draws"]
        for val in (0x12345678, 0):
            lw(h, scratch, val)
            if rd32(h, scratch) != val:
                sys.exit("FAIL: LW self-test - nothing armed (deck unchanged)")
        for p in U.POOLS:                    # table first, count last
            lw(h, p, tab_ours)
            if rd32(h, p) != tab_ours:
                for q in U.POOLS:
                    lw(h, q, U.STOCK_TAB)
                sys.exit(f"FAIL: pool {p:#x} verify - restored; REBOOT before playing")
        lw(h, U.COUNT, U.STOCK_N + U.NOURS)
        if rd32(h, U.COUNT) != U.STOCK_N + U.NOURS:
            lw(h, U.COUNT, U.STOCK_N)
            sys.exit("FAIL: count verify - restored; REBOOT before playing")
        print("RESULT: PASS - hold MENU for UTILITY, the mod rows are at the end")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
