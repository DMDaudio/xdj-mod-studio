#!/usr/bin/env python3
"""sprite_map_tour.py [CKPT ...] -- grow mods/sprite_map.json by visiting
every screen the theme should cover (emulator, native touch + keys):
main, PERFORM (+ its tabs), BROWSE, TAG LIST, INFO, MENU, back to main.
Also logs solid FILLS (colour, rect) to build/fill_log.json: UI parts drawn
from a colour constant in code, not from artwork (themes can't reach those
through the artwork tables)."""
import json
import os
import sys

sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu.panl import build_frame         # noqa: E402
import sprite_map as SM                  # noqa: E402

TOUCH = {"PERFORM": (705, 37), "BROWSE": (165, 37), "TAGLIST": (300, 37),
         "INFO": (433, 37), "MENU": (567, 37)}


def main():
    cks = sys.argv[1:] or ["cache/emu_track_loaded.pkl", "cache/emu_mainscreen.pkl"]
    blk_len = os.path.getsize("cache/blk1.bin")
    rects = {tuple(r) for r in json.load(open(SM.OUT))["rects"]} if os.path.exists(SM.OUT) else set()
    n0 = len(rects)
    fills = {}
    for ck in cks:
        m = Machine.load(ck)
        dv = m.mem.dev
        t = dv._tdg()

        def grab(frames=12):
            t.draw_log = []
            f0 = getattr(dv, "frames", 0)
            while getattr(dv, "frames", 0) < f0 + frames:
                m.run(limit=2_000_000)
            for e in t.draw_log:
                if e["kind"] == "fill":
                    k = e["colour"]
                    fills.setdefault(k, set()).add((e["x"], e["y"], e["w"], e["h"]))
                    continue
                off = (e["src"] & 0x1FFFFFFF) - SM.BASE
                st = e["sstride"] or 800
                w, h = e.get("sw", e["w"]), e.get("sh", e["h"])
                end = off + ((e["sy"] + h - 1) * st + e["sx"] + w) * 2
                if 0 <= off and end <= blk_len:
                    rects.add((off, st, e["sx"], e["sy"], w, h))
            t.draw_log = None

        def tap(xy):
            dv.panel_frame = build_frame(touch=xy)
            grab(4)
            dv.panel_frame = build_frame()
            grab(14)

        grab(20)
        for name in ("PERFORM", "PERFORM", "BROWSE", "BROWSE", "TAGLIST", "TAGLIST",
                     "INFO", "INFO", "MENU", "MENU"):
            tap(TOUCH[name])
            print(f"{ck}: after {name}: {len(rects)} rects", flush=True)
    json.dump({"block": "MAIN block1 (hdr 0x40000), offsets in bytes",
               "blk1_len": blk_len,
               "rects_fmt": ["offset", "stride_px", "sx", "sy", "w", "h"],
               "rects": sorted(rects)}, open(SM.OUT, "w"), indent=0)
    json.dump({f"{k:#06x}": sorted(v)[:40] for k, v in fills.items()},
              open("build/fill_log.json", "w"), indent=0)
    print(f"{len(rects)} rectangles ({len(rects) - n0} new) -> {SM.OUT}; "
          f"{len(fills)} fill colours -> build/fill_log.json")


if __name__ == "__main__":
    main()
