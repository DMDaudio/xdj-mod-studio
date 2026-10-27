#!/usr/bin/env python3
"""sprite_log.py [OUT] -- every rectangle of firmware ARTWORK (block1
0x08000000..) the deck copies to the screen, measured in the emulator over
the main screens (playing, PERFORM tabs, BROWSE, UTILITY, MENU). Writes
{"blk1_len", "rects": [[off, stride_px, sx, sy, w, h], ...]} -- the same
format as mods/sprite_map.json (addresses only, no pixels).

The colour themes only touch colours they recognise; the LIGHT theme must
also flip plain white/grey pictures (digits, labels), so it needs to know
which bytes are pictures: these rectangles (plus the coloured regions)."""
import json
import os
import sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "mods/sprite_map_light.json"
IMG_LO, IMG_HI = 0x08000000, 0x08000000 + 18601864


def main():
    m = Machine.load(os.environ.get("SNAP", "cache/emu_dsp_playing7.pkl"))
    mem, dev = m.mem, m.mem.dev
    t = dev.tdg
    t.draw_log = []

    def run(n, keys=(), touch=None):
        dev.panel_frame = panl.build_frame(keys=list(keys), touch=touch)
        m.cpu.run(limit=n)

    def tap(x, y):
        run(6_000_000, touch=(x, y))
        run(30_000_000)

    run(60_000_000)                                   # main screen, playing
    tap(718, 38)                                      # PERFORM
    for y in (240, 256, 272, 288):                    # its tabs
        tap(60, y)
    tap(718, 38)                                      # back
    run(15_000_000, ["Browse"]); run(60_000_000)      # BROWSE
    run(15_000_000, ["Browse"]); run(40_000_000)
    run(200_000_000, ["MenuUtility"]); run(60_000_000)   # UTILITY (hold)
    run(15_000_000, ["MenuUtility"]); run(40_000_000)
    run(15_000_000, ["MenuUtility"]); run(60_000_000)    # MENU (short)
    run(15_000_000, ["MenuUtility"]); run(40_000_000)
    rects = set()
    for d in t.draw_log:
        if d["kind"] == "fill":
            continue
        src, st = d.get("src", 0) & 0x1FFFFFFF, d.get("sstride", 0)
        if not (IMG_LO <= src < IMG_HI) or not st:
            continue
        sx, sy = d.get("sx", 0), d.get("sy", 0)
        rects.add((src - IMG_LO, st, sx, sy, d["w"], d["h"]))
    json.dump({"blk1_len": IMG_HI - IMG_LO, "rects": sorted(rects)}, open(OUT, "w"))
    print(f"{len(t.draw_log)} draws, {len(rects)} artwork rectangles -> {OUT}")


if __name__ == "__main__":
    main()


def expand(rects, w16, max_steps=24, need=0.97):
    """Packed sprite families (digits 0-9 etc.: same size, back to back):
    add same-size neighbours of each measured rectangle while the
    neighbour's pixels use the family's colours (>= need of them) --
    a neighbour that is data, not a picture, stops the walk."""
    import numpy as np
    out = set(rects)
    for off, st, sx, sy, w, h in rects:
        if st != w or sx or sy or off % 2:
            continue
        n = w * h
        base = off // 2
        fam = set(w16[base:base + n].tolist())
        for d in (1, -1):
            miss = 0
            for k in range(1, max_steps + 1):
                a = base + d * k * n
                if a < 0 or a + n > len(w16):
                    break
                px = w16[a:a + n]
                v = px.astype(np.int32)
                r, g, b = (v >> 11) & 31, ((v >> 5) & 63) >> 1, v & 31
                grey = ((np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)) <= 2) \
                    | (px == 0xF81F)
                known = np.isin(px, np.fromiter(fam, np.uint16))
                ink = (px != 0xF81F) & ((r + g + b) > 45)
                if (grey | known).mean() < need or len(set(px.tolist())) < 4 or ink.mean() < 0.01:
                    miss += 1
                    if miss >= 4:
                        break
                    continue
                miss = 0
                out.add((2 * a, st, 0, 0, w, h))
    return sorted(out)
