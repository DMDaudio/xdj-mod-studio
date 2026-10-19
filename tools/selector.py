#!/usr/bin/env python3
"""selector.py -- RELOOP/EXIT becomes the MOD MENU button (tools/mod_menu.py
reads key byte 93 bit 0x01 and opens/closes the menu).

The panel parser turns RELOOP/EXIT (payload[16] bit 0x04) into key-bitmap
byte 86 bit 0x10 at two parser variants (0x8D51BF4, 0x8D51D20):
    tst #4,r0 ; mov #86,r0 ; mov.b @(r0,r13),r0 ; bt/s ; and #0xEF,r0 ;
    or #0x10,r0 ; mov r0,r2 ; mov #86,r0 ; mov.b r2,@(r0,r13)
Re-point (4 immediates per site, 8 bytes): byte 86 -> 93, bit 0x10 -> 0x01
(free bit; standalone re-point, fail-stop per byte). The
loop RELOOP function is gone (user: loops not wanted).

  selector.py plan      deck bytes [(addr, old, new)]
  selector.py emu       emulator test (C core)
"""
import os
import sys

sys.path[:0] = [".", "tools"]

SITES = (0x08D51BF4, 0x08D51D20)


def plan_bytes():
    out = []
    for s in SITES:
        out += [(s + 2, 0x56, 0x5D),     # mov #86 -> mov #93 (load)
                (s + 8, 0xEF, 0xFE),     # and #~0x10 -> and #~0x01
                (s + 10, 0x10, 0x01),    # or #0x10 -> or #0x01
                (s + 14, 0x56, 0x5D)]    # mov #86 -> mov #93 (store)
    return out


def emu():
    from emu.machine import Machine
    from emu import panl
    import rgb_overview as RO
    import rgb_detail as RD
    m = Machine.load(os.environ.get("CKPT", "cache/emu_track_loaded.pkl"))
    mem, dev = m.mem, m.mem.dev
    for a, old, new in plan_bytes():
        assert mem.r8(a) == old, f"{a:#x} not stock"
        mem.w8(a, new)
    for a, b in RO.plan(RO.MODES["rgb"])[1]:          # overview + snapshot
        mem.w8(a, b)
    RD.install(mem)
    import detail_fake as DF                          # centre painter live
    DF.install(m, os.path.expanduser(
        "~/Downloads/Rekordbox stuff/PIONEER/USBANLZ/P000/0002D83A/"
        "ANLZ0000.EXT"), 3000)
    m.cpu.run(limit=10_000_000)
    kb = 0x0B6C7A28

    def press(name, hold=4_000_000):
        dev.panel_frame = panl.build_frame(keys=[name])
        m.cpu.run(limit=hold)
        held = (mem.r8(kb + 86), mem.r8(kb + 93), mem.r8(kb + 94))
        dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=8_000_000)
        return held

    ok = True
    for _ in range(3):
        h = press("ReloopExit")
        ok &= bool(h[1] & 0x01) and not (h[0] & 0x10)
    print(f"RELOOP held: byte93={h[1]:#04x} (want 0x01 set), byte86={h[0]:#04x}"
          f" (want 0x10 clear: stock reloop disabled)")
    h = press("LoopOut")
    print(f"OUT held: byte93={h[1]:#04x} (want 0x20 set)")
    ok &= bool(h[1] & 0x20)
    h = press("LoopIn")
    print(f"IN  held: byte94={h[2]:#04x} (want 0x01 set)")
    ok &= bool(h[2] & 0x01)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["plan"]:
        for a, o, n in plan_bytes():
            print(f"{a:#x}: {o:02x} -> {n:02x}")
        return 0
    return emu()


if __name__ == "__main__":
    sys.exit(main())
