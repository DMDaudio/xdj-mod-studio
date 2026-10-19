#!/usr/bin/env python3
"""theme_pads_check.py THEME -- EMULATOR: PERFORM pads installed + THEME
applied the deck way (tables over every region incl. our pad art); screenshot
BEAT JUMP / KEY SHIFT / STEMS tabs -> screenshots/themepads_THEME_*.png"""
import sys
sys.path[:0] = [".", "tools", "mods"]
import numpy as np                       # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu.panl import build_frame         # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import theme_switch as TS                # noqa: E402
import perform_pads as PP                # noqa: E402


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "hot-pink"
    m = Machine.load("cache/emu_track_loaded.pkl")
    mem, dv = m.mem, m.mem.dev
    PP.install(mem)
    regs, S, Ds, _ = TS.tables([name])
    lut = np.arange(65536, dtype=np.uint16)
    lut[S] = Ds[0]
    for a, n in regs:
        o = a - 0x08000000
        w = np.frombuffer(bytes(mem.ram0[o:o + 2 * n]), "<u2")
        mem.ram0[o:o + 2 * n] = lut[w].astype("<u2").tobytes()

    def tap(xy, n=40_000_000):
        dv.panel_frame = build_frame(touch=xy)
        m.run(limit=8_000_000)
        dv.panel_frame = build_frame()
        m.run(limit=n)
    tap((705, 37))
    for i, tab in enumerate(("hotcue", "beatjump", "keyshift", "stems")):
        tap((PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[i] + PP.TAB_H // 2))
        fb_png(m, f"screenshots/themepads_{name}_{tab}.png")
    print("ok")


if __name__ == "__main__":
    main()
