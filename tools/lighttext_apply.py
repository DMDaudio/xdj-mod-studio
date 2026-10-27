#!/usr/bin/env python3
"""lighttext_apply.py --survey | (install) | --revert -- the LIGHT theme
add-on on the DECK (tools/lighttext: title/BPM ink, waveform windows, screen
clears; emulator PASS: tools/light_emu_test.py, stock + FULLSET legs).

The Light THEME itself is a normal theme_switch entry: its tables ride the
regular 5e install (tools/theme_switch_apply.py) and UTILITY > THEME > LIGHT
switches the artwork. This add-on is the rest of the screen: the CPU-drawn
text, the waveform window background and the UI clear fills, plus the arming
words that make our own mods follow the theme (the pads' PDKW ink flip and
the phase meter's pm_light palette -- armed only when those modules are
installed). Runs AFTER 5e (theme switching) and 5m (phase meter).
RAM only, reboot = stock.

  --survey   READ-ONLY: the lt cave (code + bss) must read zero on THIS
             deck; writes build/lighttext/survey_ok on success
  (no flag)  chunked code write, 15 pool rewrites (stock values asserted),
             flush-chain link (lt_prev = live hook), lt_idx = LIGHT's number
             in the theme list, then the arming words
  --revert   pools back to stock, hook back to what lt chained
"""
import os
import struct
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

HOOK = 0x08C92E20
STOCK_FLUSH = 0x08E7D278
OUT = "build/lighttext"
TEXT_POOLS = (0x08C2F8FC, 0x08C31FC4)
MSET_POOLS = (0x08DC3B44, 0x08DC2808, 0x08DBC3FC)   # +overview strip
RGB_POOLS = (0x08DC2A24, 0x08DC2C74, 0x08DC2EA4, 0x08DC31A0, 0x08DC32B0,
             0x08DC3DA8, 0x08DC3FF4, 0x08DC417C, 0x08DC4470, 0x08DC4758,
             0x08DC4A24)
POOLS_STOCK = {0x08C2F8FC: 0x08C2F5C2, 0x08C31FC4: 0x08C2F5C2,
               0x08DC3B44: 0x08E1D954, 0x08DC2808: 0x08E1D954,
               0x08DBC3FC: 0x08E1D954}
for p in RGB_POOLS:
    POOLS_STOCK[p] = 0x08DB6CD4


def syms():
    s = {}
    for line in open(f"{OUT}/lt.syms"):
        a, _, n = line.split()
        s[n.lstrip("_")] = int(a, 16)
    return s


def light_idx():
    import theme_switch as TS
    return TS.default_names().index("light") + 1


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    s = syms()
    base = s["lt_head"]
    img = open(f"{OUT}/lt.bin", "rb").read()
    end = max(s.values()) + 4
    ok_file = os.path.expanduser("~/.xdj700_lt_survey_ok")   # never in the app
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, bw, read_block, send, CHUNK_B

    def rd32(h, a):
        return struct.unpack("<I", bytes(br(h, a, 4)))[0]

    def lw(h, addr, val):
        q = f"{addr:x},{val:x}LW".encode() + b"\r\x00"
        h.write(q + b"\0" * (26 - len(q)))
        drain(h, 0.5)

    if not arg and not os.path.exists(ok_file):
        sys.exit(f"REFUSED: run --survey first for {base:#x}")
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        live = rd32(h, HOOK)
        armed = rd32(h, base) == 0x5448474C          # "LGHT" entry magic
        if arg == "--survey":
            blk = read_block(h, base, end - base)
            nz = [i for i, b in enumerate(blk) if b]
            if nz:
                sys.exit(f"SURVEY FAIL: {len(nz)} nonzero bytes, first at "
                         f"{base + nz[0]:#x} - pick another LT_BASE")
            open(ok_file, "w").write("zero\n")
            print(f"SURVEY OK: {base:#x}..{end:#x} all zero")
            return 0
        if arg == "--revert":
            if not armed:
                print("not armed - nothing to do")
                return 0
            for p in TEXT_POOLS:
                if rd32(h, p) == s["lt_entry"]:
                    lw(h, p, POOLS_STOCK[p])
            for p in MSET_POOLS:
                if rd32(h, p) == s["lt_mset"]:
                    lw(h, p, POOLS_STOCK[p])
            for p in RGB_POOLS:
                if rd32(h, p) == s["lt_rgb"]:
                    lw(h, p, POOLS_STOCK[p])
            prev = rd32(h, s["lt_prev"])
            lw(h, HOOK, prev)
            lw(h, s["lt_idx"], 0)
            if rd32(h, HOOK) != prev:
                sys.exit("FAIL: hook not restored - REBOOT the deck")
            print(f"RESULT: reverted (hook = {prev:#x})")
            return 0
        # theme switching must be installed: LIGHT's number comes from it
        import theme_switch as TS
        if rd32(h, TS.TS_DATA) != 0x57535458:
            sys.exit("REFUSED: install theme switching (5e) first - "
                     "the add-on follows its LIGHT entry")
        idx = light_idx()
        print(f"flush hook = {live:#x}; lt_idx will be {idx}")
        if not armed:
            for p, stock in POOLS_STOCK.items():
                cur = rd32(h, p)
                ours = (s["lt_entry"] if p in TEXT_POOLS else
                        s["lt_mset"] if p in MSET_POOLS else s["lt_rgb"])
                if cur not in (stock, ours):
                    sys.exit(f"FAIL: pool {p:#x} = {cur:#x}, neither stock "
                             f"({stock:#x}) nor ours - nothing written")
            want = bytearray(img)
            po = s["lt_prev"] - base
            want[po:po + 4] = struct.pack("<I", live)
            cur = read_block(h, base, len(want))
            diff = [i for i, (a, b) in enumerate(zip(cur, want)) if a != b]
            if diff:
                bad = [i for i in diff if cur[i] and i not in range(po, po + 4)]
                if bad:
                    sys.exit(f"FAIL: cave +{bad[0]:#x} holds foreign data - nothing armed")
                import fastload
                left = fastload.install_region(h, base, bytes(want),
                                               fastload.SESSION_BUDGET,
                                               allow_live=range(base + po, base + po + 4),
                                               cur=cur)
                if left:
                    print(f"RESULT: PARTIAL ({left} B left) - run again")
                    return 1
            for p in TEXT_POOLS:
                lw(h, p, s["lt_entry"])
            for p in MSET_POOLS:
                lw(h, p, s["lt_mset"])
            for p in RGB_POOLS:
                lw(h, p, s["lt_rgb"])
            lw(h, HOOK, s["lt_flush"])      # the chain, LAST (atomic)
            if rd32(h, HOOK) != s["lt_flush"]:
                sys.exit("FAIL: flush hook not armed - REBOOT the deck")
        lw(h, s["lt_idx"], idx)
        # arming words for our own mods -- only where those are installed
        import perform_pads as PP
        cur = rd32(h, PP.PDKW)
        if cur in (PP.PDKSTUB, s["lt_pdk"]):
            lw(h, PP.PDKW, s["lt_pdk"])
            print(f"PDKW armed ({PP.PDKW:#x})")
        else:
            print(f"PDKW left at {cur:#x} (pads not installed)")
        try:
            pm = {}
            for line in open("build/phasemeter/pm.syms"):
                a, _, n = line.split()
                pm[n.lstrip("_")] = int(a, 16)
            pm_head = struct.unpack(
                "<I", open("build/phasemeter/pm.bin", "rb").read(4))[0]
            if rd32(h, pm["pm_entry"]) == pm_head:   # the pm blob is there
                cur = rd32(h, pm["pm_light"])
                if cur in (0, idx):
                    lw(h, pm["pm_light"], idx)
                    print("pm_light armed")
                else:
                    print(f"pm_light left at {cur} (unexpected)")
            else:
                print("pm_light skipped (phase meter not installed)")
        except OSError:
            print("pm_light skipped (no phase meter build)")
        # verify the entry magic + a pool
        if rd32(h, base) != 0x5448474C or rd32(h, TEXT_POOLS[0]) != s["lt_entry"]:
            sys.exit("FAIL: verify read mismatch - REBOOT the deck")
        print("RESULT: PASS")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
