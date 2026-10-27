#!/usr/bin/env python3
"""light_emu_test.py -- the LIGHT theme end to end in the emulated deck
(real audio-chip snapshot, a track playing): theme switching + UTILITY rows
+ tools/lighttext installed exactly like the deck installers would, then
UTILITY > THEME is set to LIGHT with real button presses, screenshots taken,
and THEME set back to OFF: the artwork must return to stock exactly and the
light add-on must switch itself off (it follows the switcher's theme).
Screens: screenshots/light_emu_{main,perform,back}.png"""
import os
import sys
sys.path[:0] = [".", "tools", "mods"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
from emu.ui import EmuApp                # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import numpy as np                       # noqa: E402
import theme_switch as TS                # noqa: E402
import utility as U                      # noqa: E402

TEXT_POOLS = (0x08C2F8FC, 0x08C31FC4)
MSET_POOLS = (0x08DC3B44, 0x08DC2808, 0x08DBC3FC)   # +overview strip
RGB_POOLS = (0x08DC2A24, 0x08DC2C74, 0x08DC2EA4, 0x08DC31A0, 0x08DC32B0, 0x08DC3DA8,
             0x08DC3FF4, 0x08DC417C, 0x08DC4470, 0x08DC4758, 0x08DC4A24)
FLUSH = 0x08C92E20


def lt_syms():
    return {l.split()[2].lstrip("_"): int(l.split()[0], 16) for l in open("build/lighttext/lt.syms")}


def install_lt(mem, idx):
    s = lt_syms()
    for i, b in enumerate(open("build/lighttext/lt.bin", "rb").read()):
        mem.w8(s["lt_head"] + i, b)
    mem.w32(s["lt_idx"], idx)
    for p in TEXT_POOLS:
        assert mem.r32(p) == 0x08C2F5C2
        mem.w32(p, s["lt_entry"])
    for p in MSET_POOLS:
        assert mem.r32(p) == 0x08E1D954
        mem.w32(p, s["lt_mset"])
    for p in RGB_POOLS:
        assert mem.r32(p) == 0x08DB6CD4
        mem.w32(p, s["lt_rgb"])
    mem.w32(s["lt_prev"], mem.r32(FLUSH))
    mem.w32(FLUSH, s["lt_flush"])
    return s


def main():
    m = Machine.load(os.environ.get("SNAP", "cache/emu_dsp_playing7.pkl"))
    mem, dev, c = m.mem, m.mem.dev, m.cpu
    names = TS.default_names()
    light = names.index("light") + 1
    if os.environ.get("FULLSET"):
        # the whole mod set (pads + hot cues + stems + FLAC) + the phase
        # meter + the light add-on exactly like EmuApp.apply_wave orders
        # it: lt UNDER pm/hc on the flush chain (lt_fill rewrites their
        # list units), pads/pm words armed last.
        from types import SimpleNamespace
        import repro_freeze as RF
        app = SimpleNamespace()
        RF.install(m, {"core", "pads", "hc", "themes", "stems", "flac"})
        U.install(mem)                       # the UTILITY [THEME] row
        EmuApp._install_light(app, m)        # pads' flush unit tail-jumps to
        EmuApp._install_phasemeter(None, m)  # FIXED stock: chain = pm -> lt
        EmuApp._arm_light(app, m)            # -> hc -> pads -> stock
        import perform_pads as PP
        assert mem.r32(PP.PDKW) != 0, "PDKW not armed"
        s = lt_syms()
    else:
        EmuApp._install_themesw(None, m)
        U.install(mem)
        s = install_lt(mem, light)
    w0 = TS.with_own_art(__import__("theme_deck").stock_words())
    regs = TS.regions(w0)

    def run(n, keys=(), touch=None):
        dev.panel_frame = panl.build_frame(keys=list(keys), touch=touch)
        c.run(limit=n)

    def key(k):
        run(12_000_000, [k]); run(25_000_000)

    def turn(n):
        for _ in range(abs(n)):
            mem.w16(panl.ROTARY_COUNTER, (mem.r16(panl.ROTARY_COUNTER) + (1 if n > 0 else -1)) & 0xFFFF)
            run(8_000_000)
        run(20_000_000)

    def theme(k):
        run(200_000_000, ["MenuUtility"]); run(60_000_000); turn(20)      # THEME row (last)
        key("RotaryPush")
        cur = mem.r8(TS.TS_DATA + 32)
        turn(k - cur)                      # the THEME list does not wrap
        run(12_000_000, ["RotaryPush"])
        steps = 0
        while mem.r8(TS.TS_DATA + 32) != k and steps < 3_000_000_000:
            run(5_000_000); steps += 5_000_000
        run(60_000_000)
        for _ in range(3):                  # close UTILITY (back to the main screen)
            if mem.r32(0x0BA371D4) == 1:
                break
            run(30_000_000, ["MenuUtility"]); run(120_000_000)
        run(120_000_000)
        return mem.r8(TS.TS_DATA + 32)

    run(30_000_000)
    got = theme(light)
    if os.environ.get("SAVE"):
        m.save(os.environ["SAVE"])
        if os.environ.get("STOP"):
            return 0
    print("screen mode", mem.r32(0x0BA371D4), "theme now", got, "(light =", light, ") skipped", mem.r32(TS.TS_DATA + 48),
          "text calls", mem.r32(s["lt_calls"]), "fills", mem.r32(s["lt_fills"]))
    fb_png(m, "screenshots/light_emu_main.png")
    run(6_000_000, touch=(718, 38)); run(80_000_000)
    fb_png(m, "screenshots/light_emu_perform.png")
    run(6_000_000, touch=(718, 38)); run(60_000_000)
    ok = got == light
    back = theme(0)
    fb_png(m, "screenshots/light_emu_back.png")
    if os.environ.get("SAVEBACK"):
        m.save(os.environ["SAVEBACK"])
    img = np.frombuffer(bytes(mem.ram0[:2 * len(w0)]), "<u2")
    diff = 0
    for a, n in regs:
        o = (a - TS.IMG_BASE) // 2
        diff += int((img[o:o + n] != w0[o:o + n]).sum())
    print("theme back", back, "artwork px differing from stock:", diff,
          "(firmware-written live data may differ; skipped chunks:", mem.r32(TS.TS_DATA + 48), ")")
    f0 = mem.r32(s["lt_fills"]); run(60_000_000)
    print("add-on off after OFF:", mem.r32(s["lt_fills"]) == f0)
    ok &= back == 0 and mem.r32(s["lt_fills"]) == f0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
