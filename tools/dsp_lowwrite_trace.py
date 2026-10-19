#!/usr/bin/env python3
"""dsp_lowwrite_trace.py SH_STEPS -- real-DSP co-sim boot; reports which DSP
pcs write below 0x00010000 (stray/NULL-based stores) and the DSP cycle at
each boot event (code seen, HINT, reset) to time the SH's ready timeout."""
import collections
import os
import sys
os.environ["XDJ_DSP_EMU"] = "1"
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F
from emu.c6x import dspdev

LOW = collections.Counter()
FIRST = {}
EV = []
_ev = dspdev.DspDevice.ev


def ev(self, *w):
    if w[0] != "hpic-w":
        EV.append((self.cpu.cycle, self.cycles) + w)
    return _ev(self, *w)


dspdev.DspDevice.ev = ev
flash, _ = F.load_flash(None)
m = Machine(flash)
d = m.mem.dev.dspemu


def watch(kind, a, n, v):
    if kind == "w" and a < 0x10000:
        pc = d.cpu.pc
        LOW[(pc, a)] += 1
        FIRST.setdefault((pc, a), (d.cycles, v))


d.bus.watch = watch
steps = 0
while steps < int(float(sys.argv[1])):
    m.cpu.run(limit=10_000_000)
    steps += 10_000_000
for e in EV[-25:]:
    print("EV", e)
for (pc, a), n in LOW.most_common(15):
    print(f"low write pc {pc:#x} addr {a:#x} x{n} first at dsp cycle {FIRST[(pc, a)][0]} val {FIRST[(pc, a)][1]:#x}")
print("DSP pc", hex(d.cpu.pc), "cycles", d.cycles, "err", d.error)
