#!/usr/bin/env python3
"""flac_waitload.py -- load full.flac, then run long; TIME advance = decode."""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

m = Machine.load("/tmp/flac_full_ready.pkl")   # C core for speed
mem, dev = m.mem, m.mem.dev
ROT = 0x0B6C7B48


def key(k, n=25_000_000):
    dev.panel_frame = panl.build_frame(keys=[k])
    m.cpu.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=n)


def push(tag, n=40_000_000):
    key("RotaryPush", n)
    fb_png(m, f"screenshots/flac_w_{tag}.png")
    print(tag, flush=True)


mem.w16(ROT, (mem.r16(ROT) + 0) & 0xFFFF)   # keep first row
m.cpu.run(limit=15_000_000)
push("enter")     # [FOLDER] -> Contents
mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)   # Contents -> Lifelike folder
m.cpu.run(limit=15_000_000)
push("artist")    # into Lifelike
push("load")      # load full.flac (only file)
for i in range(6):
    m.cpu.run(limit=500_000_000)
    fb_png(m, f"screenshots/flac_w_t{i}.png")
    print(f"t{i} shot", flush=True)
m.save("/tmp/flac_waitdone.pkl")
print("done", flush=True)
