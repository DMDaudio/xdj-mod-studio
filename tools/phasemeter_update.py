#!/usr/bin/env python3
"""phasemeter_update.py OLD_SYMS [OLD_BIN] -- replace a LIVE phase meter on the deck
with the current build (build/phasemeter), when the new build's layout
moved (pm_prev etc. at other addresses than OLD_SYMS says).

Session 1: hook read == pm_entry -> hook back to the OLD pm_prev (LW,
verified) -> the whole code + state area [pm_entry, _end) read and brought
to the new image with its state zeroed, pm_prev = what the old one chained,
pm_mode = 1 (any byte may change: it is all our own area) -> verified.
Session 2 (fresh, paced): LW the hook to pm_entry, verify.
Refuses unless the hook is ours (or already reverted to the old chain).
RAM only, reboot = stock.
"""
import struct
import subprocess
import sys
sys.path.insert(0, "tools")

HOOK = 0x08C92E20
OUT = "build/phasemeter"


def load_syms(path):
    s = {}
    for line in open(path):
        a, _, n = line.split()
        s[n.lstrip("_")] = int(a, 16)
    return s


def main():
    old = load_syms(sys.argv[1])
    new = load_syms(f"{OUT}/pm.syms")
    base = new["pm_entry"]
    assert old["pm_entry"] == base
    nm = subprocess.run(["work/sh-tools/bin/sh-elf-nm", f"{OUT}/pm.elf"],
                        capture_output=True, text=True).stdout
    end = next(int(l.split()[0], 16) for l in nm.splitlines() if l.endswith(" _end"))
    end = (end + 3) & ~3
    img = open(f"{OUT}/pm.bin", "rb").read()
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain, end_shared
    from rgb_overview_apply import br, send
    import fastload
    rd32 = lambda h, a: struct.unpack("<I", fastload.BR32(h, a, 4))[0]

    def lw(h, a, v):
        fastload.lw_words(h, a, v, drain_after=True)

    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        live = rd32(h, HOOK)
        prev_old = rd32(h, old["pm_prev"])
        print(f"hook {live:#x}, old pm_prev {prev_old:#x}")
        if live == base:
            lw(h, HOOK, prev_old)
            if rd32(h, HOOK) != prev_old:
                lw(h, HOOK, base)
                sys.exit("FAIL: hook did not move - restored, nothing written")
            print(f"unhooked: hook = {prev_old:#x}")
        elif live != prev_old:
            sys.exit(f"FAIL: hook {live:#x} is neither ours nor the old chain")
        want = bytearray(end - base)
        want[:len(img)] = img
        po = new["pm_prev"] - base
        want[po:po + 4] = struct.pack("<I", prev_old)
        want[new["pm_mode"] - base] = 1
        cur = bytearray(fastload.BR32(h, base, len(want)))
        if len(sys.argv) > 2:                    # the old build's code must be there
            ob = open(sys.argv[2], "rb").read()
            if bytes(cur[:0x400]) != ob[:0x400]:
                sys.exit("FAIL: deck code is not the old build - nothing written "
                         "(meter left unhooked: rerun or reboot)")
        left = fastload._write_window(h, base, bytes(want), cur,
                                      allow_live=range(base, end))
        got = fastload.BR32(h, base, len(want))
        if got != bytes(want):
            bad = next(i for i, (a, b) in enumerate(zip(got, want)) if a != b)
            sys.exit(f"FAIL: verify at {base + bad:#x} - meter stays unhooked (safe)")
        print(f"new code + state written ({len(want)} B, left {left})")
    finally:
        h.close()
        end_shared()
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)   # fresh, paced session
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        if rd32(h, HOOK) != prev_old or rd32(h, new["pm_prev"]) != prev_old:
            sys.exit("FAIL: state changed between sessions - nothing armed")
        lw(h, HOOK, base)
        if rd32(h, HOOK) != base:
            lw(h, HOOK, prev_old)
            sys.exit("FAIL: hook verify - restored to the chain; meter off")
        print("RESULT: PASS - new phase meter armed")
    finally:
        h.close()
        end_shared()


if __name__ == "__main__":
    main()
