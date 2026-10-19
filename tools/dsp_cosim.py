#!/usr/bin/env python3
"""dsp_cosim.py [max_SH_steps] -- boot the SH from reset with the REAL DSP
attached (XDJ_DSP_EMU=1): Pioneer's stage-1 + main DSP image load through
the genuine HPI/GPIO handshake. Prints both sides' progress per stage.
"""
import os
import sys
import time
os.environ["XDJ_DSP_EMU"] = "1"
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F

MAX = int(float(sys.argv[1])) if len(sys.argv) > 1 else 2_000_000_000
STAGE = 10_000_000
flash, info = F.load_flash(None)
m = Machine(flash)
dev = m.mem.dev
d = dev.dspemu
steps, t0 = 0, time.time()
while steps < MAX:
    m.cpu.run(limit=STAGE)
    steps += STAGE
    c = d.cpu
    print(f"SH {steps/1e6:6.0f}M pc={m.cpu.pc:#x} | DSP reset={int(d.in_reset)} run={int(d.running)} "
          f"cyc={c.cycle/1e6:.2f}M pc={c.pc:#x} hpic={d.hpic:#x} hpia={d.hpia:#x} "
          f"sp={'Y' if c.sp else '-'} err={d.error} [{time.time()-t0:.0f}s]", flush=True)
    if d.error:
        break
for e in d.log[:60]:
    print("  ", e)
print("unknown DSP MMIO:", sorted(((k[0], hex(k[1])), v) for k, v in d.bus.unknown.items())[:40])
