#!/usr/bin/env python3
"""theme_screens_check.py THEME [MAP] -- EMULATOR: apply THEME exactly as the
deck tables would (theme_switch.tables + model_switch on the artwork in RAM),
then screenshot main + PERFORM and count pixels the Studio preview would
theme (blue-ish) but the deck leaves stock. Writes screenshots/themecheck_*."""
import os
import sys
sys.path[:0] = [".", "tools", "mods"]
import numpy as np                       # noqa: E402
from PIL import Image                    # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu.panl import build_frame         # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import theme_switch as TS                # noqa: E402
import themes as T                       # noqa: E402


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "hot-pink"
    tag = sys.argv[2] if len(sys.argv) > 2 else "now"
    regs, S, Ds, _ = TS.tables([name])
    m = Machine.load("cache/emu_track_loaded.pkl")
    mem, dv = m.mem, m.mem.dev
    lut = np.arange(65536, dtype=np.uint16)
    lut[S] = Ds[0]
    for a, n in regs:
        o = a - 0x08000000
        w = np.frombuffer(bytes(mem.ram0[o:o + 2 * n]), "<u2")
        mem.ram0[o:o + 2 * n] = lut[w].astype("<u2").tobytes()
    tot = 0
    for scr, xy in (("main", None), ("perform", (705, 37))):
        if xy:
            dv.panel_frame = build_frame(touch=xy)
            m.run(limit=8_000_000)
            dv.panel_frame = build_frame()
        m.run(limit=60_000_000)
        p = f"screenshots/themecheck_{tag}_{name}_{scr}.png"
        fb_png(m, p)
        a = np.array(Image.open(p).convert("RGB")).astype(np.uint32)
        c = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        want = T.map_colours(c.astype(np.uint16).ravel(), T.THEMES[name]).reshape(c.shape)
        miss = want != c                       # still a colour the theme changes
        # mark misses red in a copy
        b = a.astype(np.uint8).copy()
        b[miss] = (255, 0, 0)
        Image.fromarray(b).save(p.replace(".png", "_miss.png"))
        print(f"{scr}: {int(miss.sum())} px still stock-blue -> {p}")
        tot += int(miss.sum())
    print("TOTAL missed px:", tot)


if __name__ == "__main__":
    main()
