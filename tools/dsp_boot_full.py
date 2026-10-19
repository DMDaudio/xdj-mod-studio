#!/usr/bin/env python3
"""dsp_boot_full.py [FROM_PKL] [TOTAL_SH_STEPS] -- continue a real-DSP boot
(XDJ_DSP_EMU snapshot from tools/dsp_ready_run.py SAVE=...) to the idle
main screen, saving cache/emu_dsp_idle.pkl every 1e9 steps and printing
DSP health + a screenshot (screenshots/dsp_boot.png) each time."""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from ghosttap_emu import fb_png

src = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_boot.pkl"
total = int(float(sys.argv[2])) if len(sys.argv) > 2 else 6_000_000_000
m = Machine.load(src)
d = m.mem.dev.dspemu
t0, done = time.time(), 0
while done < total:
    m.cpu.run(limit=100_000_000)
    done += 100_000_000
    print(f"+{done/1e6:6.0f}M SH pc={m.cpu.pc:#x} | DSP cyc={d.cycles/1e6:8.1f}M "
          f"pc={d.cpu.pc:#x} err={d.error} [{time.time()-t0:.0f}s]", flush=True)
    if d.error:
        break
    if done % 1_000_000_000 == 0:
        m.save("cache/emu_dsp_idle.pkl")
        fb_png(m, "screenshots/dsp_boot.png")
        print("  saved cache/emu_dsp_idle.pkl + screenshots/dsp_boot.png", flush=True)
m.save("cache/emu_dsp_idle.pkl")
fb_png(m, "screenshots/dsp_boot.png")
print("DONE", flush=True)
