#!/usr/bin/env python3
"""dsp_capture.py [max_steps] -- what does the SH load into the DSP at boot?

Boots stock fw 1.15 from reset (C core, no HLE) and collects everything
the SH writes through the HPI bridge: bulk DMA into the auto-increment
FIFO (sh7724.dsp_write, lands at the live HPIA) + single-word data-port
writes (C core HPID log). Stops once the bulk count has been flat for a
while after the DSP run flag (0x1183FFF0) was written.

Output: cache/dsp_boot.pkl = {"pages": {page>>16: bytes}, "words":
[(dsp_addr, value), ...] in order, "ctrl": [(step, hpic), ...],
"bulk": n, "steps": n}. Input for the C674x emulator (emu/c6x).
"""
import os
import pickle
import sys
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F

MAX = int(float(sys.argv[1])) if len(sys.argv) > 1 else 3_000_000_000
STAGE = 25_000_000
flash, info = F.load_flash(None)
m = Machine(flash)
dev = m.mem.dev
dev.cs6_py = True      # all HPI traffic through the Python device
ctrl, words, last_bulk, flat, steps = [], [], -1, 0, 0
while steps < MAX:
    m.cpu.run(limit=STAGE)
    steps += STAGE
    cpu = m.cpu
    words = list(getattr(dev, "hpi_words", []))
    if hasattr(cpu, "_cs6_out"):
        cpu._cs6_out()
    c = dev.cs6
    if not ctrl or ctrl[-1][1] != c["ctrl"]:
        ctrl.append((steps, c["ctrl"]))
    bulk = c.get("bulk", 0)
    print(f"{steps/1e6:7.0f}M pc={cpu.pc:#x} bulk={bulk} words={len(words)} "
          f"hpic={c['ctrl']:#x} run={c.get('run', 0)} "
          f"pages={len(getattr(dev, 'dspmem', {}))}", flush=True)
    flat = flat + 1 if bulk == last_bulk else 0
    last_bulk = bulk
    if c.get("run") and flat >= 8:
        break
pages = {k: bytes(v) for k, v in getattr(dev, "dspmem", {}).items()}
# (data-port writes already landed in dspmem in order; "words" keeps
# the full (addr, size, value) sequence incl. later mailbox traffic)
os.makedirs("cache", exist_ok=True)
with open("cache/dsp_boot.pkl", "wb") as f:
    pickle.dump({"pages": pages, "words": words,
                 "seq": list(getattr(dev, "hpi_seq", [])), "ctrl": ctrl,
                 "bulk": last_bulk, "steps": steps}, f)
print("saved cache/dsp_boot.pkl:", {hex(k << 16): sum(1 for b in v if b)
                                     for k, v in pages.items()})
