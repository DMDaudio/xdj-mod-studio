#!/usr/bin/env python3
"""cave_screen_test.py ADDR SIZE (hex) -- emulator: fill a candidate cave
region with junk, run load/browse/taglist, and compare the screen with an
untouched run (display hardware reads memory directly, so a CPU
read-watch is not enough -- the 0x088BA000 lesson, CLAUDE.md §38)."""
import sys
sys.path[:0] = [".", "tools"]
import numpy as np
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png
from PIL import Image
base, size = int(sys.argv[1], 16), int(sys.argv[2], 16)


def run(junk):
    m = Machine.load("cache/emu_nav_daveMYM.pkl"); mem = m.mem; dev = mem.dev
    o = base - 0x08000000
    assert not any(mem.ram0[o:o + size]), "region not zero in emu"
    if junk:
        mem.ram0[o:o + size] = bytes([0x5A, 0xA5, 0x3C, 0xC3]) * (size // 4)
    for k, n in (("RotaryPush", 40_000_000), ("Browse", 40_000_000),
                 ("Taglist", 30_000_000), ("Browse", 30_000_000)):
        dev.panel_frame = panl.build_frame(keys=[k]); m.cpu.run(limit=3_000_000)
        dev.panel_frame = panl.build_frame(); m.cpu.run(limit=n)
    fb_png(m, "/tmp/ct.png")
    a = np.asarray(Image.open("/tmp/ct.png").convert("RGB")).astype(int)
    intact = bytes(mem.ram0[o:o + size]) == (
        bytes([0x5A, 0xA5, 0x3C, 0xC3]) * (size // 4) if junk else bytes(size))
    return a, intact


a0, _ = run(False)
a1, ok = run(True)
print(f"{base:#x}+{size:#x}: screen diff px {int((np.abs(a0 - a1).sum(2) > 0).sum())}, "
      f"untouched by firmware: {ok}")
