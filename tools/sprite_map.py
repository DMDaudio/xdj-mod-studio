#!/usr/bin/env python3
"""sprite_map.py CHECKPOINT [FRAMES] [TOUCH ...]  ->  mods/sprite_map.json

Asks the firmware which parts of block1 are pictures: runs the emulator
from a booted checkpoint with the 2DG draw log on and records every image
blit whose source lies inside the block1 image (loaded at 0x08000000).
Those rectangles are artwork by construction -- the graphics engine copies
them to the screen -- so the theme engine recolours them whole, instead of
relying only on its per-window guess (which rejects smooth gradients as
lookup tables and left buttons half-themed).

TOUCH names (emu/panl.py keys, e.g. MenuUtility Taglist) are pressed one
after another to visit more screens; new rectangles merge into the file.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "mods", "sprite_map.json")
BASE = 0x08000000


def collect(m, frames, rects, blk_len):
    dv = m.mem.dev
    t = dv._tdg()
    t.draw_log = []
    f0 = getattr(dv, "frames", 0)
    while getattr(dv, "frames", 0) < f0 + frames:
        m.run(limit=2_000_000)
    for e in t.draw_log:
        if e["kind"] == "fill":
            continue
        off = (e["src"] & 0x1FFFFFFF) - BASE
        st = e["sstride"] or 800
        w = e.get("sw", e["w"])
        h = e.get("sh", e["h"])
        end = off + ((e["sy"] + h - 1) * st + e["sx"] + w) * 2
        if 0 <= off and end <= blk_len:
            rects.add((off, st, e["sx"], e["sy"], w, h))
    t.draw_log = None


def main():
    from emu.machine import Machine
    from emu.panl import build_frame
    ck = sys.argv[1]
    frames = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    touches = sys.argv[3:]
    blk_len = os.path.getsize(os.path.join(ROOT, "cache", "blk1.bin"))
    rects = set()
    if os.path.exists(OUT):
        rects = {tuple(r) for r in json.load(open(OUT))["rects"]}
    m = Machine.load(ck)
    n0 = len(rects)
    collect(m, frames, rects, blk_len)
    for k in touches:
        m.mem.dev.panel_frame = build_frame([k])
        collect(m, 10, rects, blk_len)
        m.mem.dev.panel_frame = build_frame([])
        collect(m, frames, rects, blk_len)
    json.dump({"block": "MAIN block1 (hdr 0x40000), offsets in bytes",
               "blk1_len": blk_len,
               "rects_fmt": ["offset", "stride_px", "sx", "sy", "w", "h"],
               "rects": sorted(rects)}, open(OUT, "w"), indent=0)
    print(f"{len(rects)} rectangles ({len(rects) - n0} new) -> {OUT}")


if __name__ == "__main__":
    main()
