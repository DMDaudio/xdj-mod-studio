#!/usr/bin/env python3
"""play_sustain.py SNAP -- restore a mid-play snapshot and just run,
reporting pos/mode/dac every chunk. Is the SH4 feeder alive long-term?
"""
import os
import sys
import time

sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402

SNAP = sys.argv[1]
STEPS = int(float(os.environ.get("STEPS", "1.2e9")))
CHUNK = int(float(os.environ.get("CHUNK", "1e8")))
SH_POS = 0x09D246E0
MODE = 0x0B6C4264

m = Machine.load(SNAP)
mem, dev = m.mem, m.mem.dev
d = dev.dspemu
t0 = time.time()
done = 0
while done < STEPS:
    m.cpu.run(limit=CHUNK)
    done += CHUNK
    disk = getattr(dev, "usb_disk", None)
    acc = len(getattr(disk, "access", [])) if disk else -1
    print(f"+{done/1e6:.0f}M pos={mem.r32(SH_POS)} mode={mem.r8(MODE)} "
          f"dac={len(d.asp1.out)} dsp err={d.error} disk={acc} "
          f"[{time.time()-t0:.0f}s]", flush=True)
