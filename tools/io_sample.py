#!/usr/bin/env python3
"""io_sample.py -- count which device addresses cross to Python while a
snapshot runs, to size the candidates for a native fastcore fast path.

Usage: .venv/bin/python tools/io_sample.py <snapshot> [steps] [out.json]
"""
import json
import sys
import time
from collections import Counter

sys.path.insert(0, ".")
from emu.machine import Machine

STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 60_000_000
snap = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_wavplay.pkl.gz"
out = sys.argv[3] if len(sys.argv) > 3 else "cache/io_sample.json"

m = Machine.load(snap)
cpu = m.cpu
mem = m.mem

rd, wr = Counter(), Counter()
bytes_rd, bytes_wr = Counter(), Counter()

orig_rd_cbs, orig_wr_cbs = cpu._cb_rd, cpu._cb_wr  # keep refs alive

import emu.fastcpu as fc


def cbrds(a, n):
    try:
        v = mem.dev.read(a, n)
    except Exception:
        v = 0
    rds[(a & 0x1FFFFFFF) >> 8] += 1
    return v


def cbwrs(a, v, n):
    try:
        before = len(mem.dev.pending)
        for i in range(n):
            mem.dev.write(a + i, 1, (v >> (8 * i)) & 0xFF)
        if len(mem.dev.pending) != before or (a & 0x1FFF0000) == 0x04080000:
            cpu.core.io_break = 1
    except Exception:
        cpu.core.io_break = 1
    wrs[(a & 0x1FFFFFFF) >> 8] += 1


rd, wr, rds, wrs = Counter(), Counter(), Counter(), Counter()


def cbrd(a):
    try:
        v = mem.dev.read(a, 1) & 0xFF
    except Exception:
        v = 0
    rd[(a & 0x1FFFFFFF) >> 4] += 1
    bytes_rd[(a & 0x1FFFFFFF)] += 1
    return v


def cbwr(a, v):
    try:
        before = len(mem.dev.pending)
        mem.dev.write(a, 1, v & 0xFF)
        if len(mem.dev.pending) != before or (a & 0x1FFF0000) == 0x04080000:
            cpu.core.io_break = 1
    except Exception:
        cpu.core.io_break = 1
    wr[(a & 0x1FFFFFFF) >> 4] += 1
    bytes_wr[(a & 0x1FFFFFFF)] += 1


cpu.core.io_rd = fc.IO_RD(cbrd)
cpu.core.io_wr = fc.IO_WR(cbwr)
cpu.core.io_rds = fc.IO_RDS(cbrds)
cpu.core.io_wrs = fc.IO_WRS(cbwrs)

t0 = time.time()
m.cpu.run(limit=STEPS)
t1 = time.time()
print(f"{STEPS:,} steps in {t1-t0:.2f}s (slowed by the sampler)")

print("\n== crossings by 16-byte bucket (reads) ==")
for k, n in rd.most_common(30):
    print(f"  {k<<4:#010x}  x{n}")
print("\n== crossings by 16-byte bucket (writes) ==")
for k, n in wr.most_common(30):
    print(f"  {k<<4:#010x}  x{n}")
print(f"\ntotal byte crossings: reads {sum(rd.values()):,} writes {sum(wr.values()):,}")
print("== word crossings by 256-byte bucket (reads io_rds) ==")
for k, n in rds.most_common(25):
    print(f"  {k<<8:#010x}  x{n}")
print("== word crossings by 256-byte bucket (writes io_wrs) ==")
for k, n in wrs.most_common(25):
    print(f"  {k<<8:#010x}  x{n}")
print(f"total word crossings: reads {sum(rds.values()):,} writes {sum(wrs.values()):,}")

if out:
    with open(out, "w") as f:
        json.dump({"rd": {f"{k<<4:#x}": n for k, n in rd.most_common(200)},
                   "wr": {f"{k<<4:#x}": n for k, n in wr.most_common(200)},
                   "total_rd": sum(rd.values()), "total_wr": sum(wr.values())}, f, indent=1)
    print("saved", out)
