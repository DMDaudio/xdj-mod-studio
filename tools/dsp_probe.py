#!/usr/bin/env python3
"""dsp_probe.py ADDR=VAL [ADDR=VAL ...] -- emulator: answer chosen DSP (HPI)
shared variables with fixed values, load + play Make Your Move, and report
screen changes in the REMAIN and centre-waveform areas (PNG saved).
Behavioural-DSP-model groundwork (CLAUDE.md §40)."""
import sys
sys.path[:0] = [".", "tools"]
import numpy as np
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png
from PIL import Image

fixed = {int(a, 16): int(v, 16) for a, v in (x.split("=") for x in sys.argv[1:])}
m = Machine.load("cache/emu_nav_daveMYM.pkl")
dev = m.mem.dev
o_r = dev._cs6_read


def rd(a, size):
    off = a - 0x18000000
    if 0xC0000 <= off < 0xC0004 and dev.cs6["addr"] in fixed:
        return fixed[dev.cs6["addr"]]
    return o_r(a, size)


dev._cs6_read = rd


def key(k, n):
    dev.panel_frame = panl.build_frame(keys=[k])
    m.cpu.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=n)


key("RotaryPush", 150_000_000)
key("Browse", 40_000_000)
key("PlayPause", 150_000_000)
tag = "_".join(f"{a:x}-{v:x}" for a, v in fixed.items()) or "none"
out = f"screenshots/dsp_probe_{tag}.png"
fb_png(m, out)
im = np.asarray(Image.open(out).convert("L")).astype(int)
print(f"{tag}: remain {im[360:395,290:470].sum()} centre "
      f"{im[170:320,110:620].sum()} -> {out}", flush=True)
