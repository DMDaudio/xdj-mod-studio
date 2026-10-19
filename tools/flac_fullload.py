#!/usr/bin/env python3
"""flac_fullload.py -- navigate to the forged FLAC and load it (traced)."""
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

m = Machine.load("/tmp/flac_full_ready.pkl", core="py")
mem, dev, c = m.mem, m.mem.dev, m.cpu
ROT = 0x0B6C7B48
hits = {"gate": 0, "pay": 0}


def tr(pc, op):
    if GATE_LO <= pc < GATE_HI:
        hits["gate"] += 1
    if PAY_LO <= pc < PAY_HI:
        hits["pay"] += 1
        if hits["pay"] <= 3:
            print("PAYLOAD ENTERED", flush=True)


def key(k, n=25_000_000):
    dev.panel_frame = panl.build_frame(keys=[k])
    c.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    c.run(limit=n)


def push(tag):
    key("RotaryPush", 40_000_000)
    fb_png(m, f"screenshots/flac_{tag}.png")
    print(f"{tag}: gate={hits['gate']} pay={hits['pay']}", flush=True)


c.trace = tr
push("folder")      # [FOLDER] -> Contents
push("contents")    # Contents -> Lifelike...
push("artist")      # -> track
push("load")        # load it
c.trace = None
print(f"FINAL gate={hits['gate']} pay={hits['pay']}", flush=True)
print("RESULT:", "PASS" if hits["pay"] > 0 else "FAIL", flush=True)
