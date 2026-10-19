#!/usr/bin/env python3
"""flac_load.py -- from the FlacTest listing, select test.flac and load it."""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

GATE_LO, GATE_HI = 0x080FB800, 0x080FC200
PAY_LO = 0x091BD7B0
PAY_HI = 0x091BD7B0 + 53228

m = Machine.load("/tmp/flac_ready.pkl", core="py")
mem, dev, c = m.mem, m.mem.dev, m.cpu
ROT = 0x0B6C7B48
mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)   # control.wav -> test.flac
m.cpu.run(limit=15_000_000)
fb_png(m, "screenshots/flac_sel.png")
hits = {"gate": 0, "pay": 0}

def tr(pc, op):
    if GATE_LO <= pc < GATE_HI:
        hits["gate"] += 1
    if PAY_LO <= pc < PAY_HI:
        hits["pay"] += 1
        if hits["pay"] == 1:
            print("PAYLOAD ENTERED", flush=True)

c.trace = tr
dev.panel_frame = panl.build_frame(keys=["RotaryPush"])
m.cpu.run(limit=5_000_000)
dev.panel_frame = panl.build_frame()
m.cpu.run(limit=int(os.environ.get("STEPS", "120000000")))
c.trace = None
fb_png(m, "screenshots/flac_loaded.png")
print(f"gate hits: {hits['gate']}, payload hits: {hits['pay']}", flush=True)
print("RESULT:", "PASS" if hits["pay"] > 0 else "FAIL", flush=True)
