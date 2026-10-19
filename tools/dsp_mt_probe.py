#!/usr/bin/env python3
"""dsp_mt_probe.py SNAPSHOT -- with Pioneer's REAL DSP playing a track in the
emulator: which command-block words (0x11837B00..0x11838800) does the DSP
read, from which DSP code, and what changes when MASTER TEMPO toggles?
Goal: find the stretcher's pitch / rate inputs for live key shift."""
import collections
import os
import sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

m = Machine.load(sys.argv[1])
d, dev = m.mem.dev.dspemu, m.mem.dev
LO, HI = 0x11837B00, 0x11838800


def window(tag, steps=60e6):
    rd = collections.Counter()
    val = {}
    def w(kind, a, n, v):
        if kind == "r" and LO <= a < HI:
            rd[(a, d.cpu.pc & ~0x3F)] += 1
            val[a] = v
    d.bus.watch = w
    m.cpu.run(limit=int(steps))
    d.bus.watch = None
    words = collections.Counter()
    for (a, pc), n in rd.items():
        words[a] += n
    print(f"--- {tag}: DSP read {len(words)} mailbox words")
    for a, n in sorted(words.items()):
        pcs = sorted({hex(pc) for (aa, pc) in rd if aa == a})[:4]
        print(f"   {a:#x} x{n} = {val[a]:#010x}  from {pcs}")
    return {a: val[a] for a in words}


dev.panel_frame = panl.build_frame(tempo=250)
a = window("MT as loaded")
dev.panel_frame = panl.build_frame(keys=["MasterTempo"], tempo=250)
m.cpu.run(limit=3_000_000)
dev.panel_frame = panl.build_frame(tempo=250)
b = window("after MASTER TEMPO toggle")
print("--- changed words:")
for k in sorted(set(a) | set(b)):
    if a.get(k) != b.get(k):
        print(f"   {k:#x}: {a.get(k, 0):#010x} -> {b.get(k, 0):#010x}")
