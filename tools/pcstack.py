#!/usr/bin/env python3
"""pcstack.py PC [PC...] -- emulator: run from CKPT (py core), after KEYS
(comma list) presses, and print r4-r7 + code addresses found on the stack
each time PC is hit (MAXH hits). Call-chain finder."""
import os, sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl
from emu.sh4 import PcHalt
m = Machine.load(os.environ["CKPT"], core="py"); c = m.cpu; mem = m.mem
pcs = {int(x, 16) for x in sys.argv[1:]}
MAX = int(os.environ.get("MAXH", "5")); SKIP = int(os.environ.get("SKIP", "0"))
n = [0]
def iscode(v): return 0x08080000 <= v < 0x091B0000
def tr(pc, op):
    if pc in pcs:
        n[0] += 1
        if n[0] <= SKIP: return
        sp = c.gr(15)
        st = [f"{mem.r32((sp+i)&0x1FFFFFFF):#x}" for i in range(0, 0x400, 4)
              if iscode(mem.r32((sp+i)&0x1FFFFFFF))]
        print(f"HIT {pc:#x} step {c.steps} r4-7 {[hex(c.gr(r)) for r in (4,5,6,7)]} pr {c.pr:#x}\n   stack: {' '.join(st[:20])}", flush=True)
        if n[0] >= SKIP + MAX: raise PcHalt(pc)
for k in [x for x in os.environ.get("KEYS", "").split(",") if x]:
    mem.dev.panel_frame = panl.build_frame(keys=[k]); c.run(limit=3_000_000)
    mem.dev.panel_frame = panl.build_frame(); c.run(limit=int(os.environ.get("GAP", "2000000")))
c.trace = tr
print(c.run(limit=int(os.environ.get("STEPS", "50000000"))))
print("hits", n[0])
