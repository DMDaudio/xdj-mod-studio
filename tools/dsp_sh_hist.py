#!/usr/bin/env python3
"""dsp_sh_hist.py -- PC histogram of the SH during real-DSP play.

Runs the Python SH core briefly (trace hook counts PCs, nothing stored),
so we can see what dominates the instruction stream (poll loops etc.).
Env: SNAP=... N=3 (million instructions)
"""
import collections
import os
import sys
import time

sys.path.insert(0, ".")

from emu.machine import Machine

SNAP = os.environ.get("SNAP", "cache/emu_dsp_wavplay.pkl.gz")
N = int(float(os.environ.get("N", "3"))) * 1_000_000

m = Machine.load(SNAP)
de = m.mem.dev.dspemu
hist = collections.Counter()


def tr(pc, op):
    hist[pc] += 1
    return None


de.sync()
cpu = m.cpu
cpu.trace = tr
t0 = time.time()
cpu.run(limit=N)          # trace set -> routes to the Python core
dt = time.time() - t0
cpu.trace = None
tot = sum(hist.values())
print(f"{tot:,} instructions in {dt:.1f}s "
      f"({tot / dt / 1e6:.2f}M/s python-core)")
print("top 30 PCs:")
for pc, n in hist.most_common(30):
    print(f"  {pc:08X}  {n / tot * 100:5.1f}%  {n:,}")
# page-level rollup
pg = collections.Counter()
for pc, n in hist.items():
    pg[pc >> 14] += n
print("top pages (64K):")
for p, n in pg.most_common(10):
    print(f"  {p << 14:08X}  {n / tot * 100:5.1f}%")
