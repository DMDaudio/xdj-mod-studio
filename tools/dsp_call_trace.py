#!/usr/bin/env python3
"""dsp_call_trace.py SH_STEPS HEXPC [HEXPC...] -- boot the SH with the real
DSP (XDJ_DSP_EMU=1) and log every time the DSP reaches one of the given
addresses: cycle, return address (B3), A4..A6 / B4..B6 (C6000 args).
Used to find who passes the main image's table builder a NULL pointer."""
import os
import sys
os.environ["XDJ_DSP_EMU"] = "1"
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F
from emu.c6x import cpu as C

WATCH = {int(x, 16) for x in sys.argv[2:]}
HITS = []
_init = C.Cpu.__init__


def init(self, *a, **k):
    _init(self, *a, **k)

    def tr(cpu, pc, ep):
        if pc in WATCH and len(HITS) < 200:
            r = cpu.r
            HITS.append((cpu.cycle, hex(pc), "B3", hex(r[35]), "A4-6",
                         [hex(r[i]) for i in (4, 5, 6)], "B4-6",
                         [hex(r[i]) for i in (36, 37, 38)]))
    self.trace = tr


C.Cpu.__init__ = init
flash, _ = F.load_flash(None)
m = Machine(flash)
steps = 0
while steps < int(float(sys.argv[1])):
    m.cpu.run(limit=10_000_000)
    steps += 10_000_000
for h in HITS:
    print(*h)
d = m.mem.dev.dspemu
print("DSP pc", hex(d.cpu.pc), "cycles", d.cycles, "err", d.error)
