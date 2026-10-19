#!/usr/bin/env python3
"""dsp_mailbox_readers.py SNAPSHOT [SH_STEPS] -- which DSP code reads (and
writes) the SH->DSP command block while a track plays.

Runs the snapshot on the PYTHON DSP core (needs per-instruction pcs) and
logs every DSP load/store inside LO..HI (default: channel-1 command block
0x11837BC0..+0x80) with the execute-packet pc that issued it. Also logs
stores of the values that came from there? no -- keep it simple: print a
table addr -> {pc: count} for reads and writes.

Env: LO, HI (hex), SET=addr=val,... (SH-side writes before running, e.g.
to force MT on or a Q20 ratio).
"""
import collections
import os
import sys
sys.path[:0] = [".", "tools"]
os.environ["XDJ_DSP_CORE"] = "py"
from emu.machine import Machine          # noqa: E402
from emu.c6x import cpu as C             # noqa: E402

SNAP = sys.argv[1]
STEPS = int(float(sys.argv[2])) if len(sys.argv) > 2 else 50_000_000
LO = int(os.environ.get("LO", "0x11837BC0"), 16)
HI = int(os.environ.get("HI", "0x11837C40"), 16)

rd = collections.defaultdict(collections.Counter)
wr = collections.defaultdict(collections.Counter)
vals = collections.defaultdict(set)

orig_ld, orig_st = C.Cpu.ld, C.Cpu.st


ALIAS = (LO - 0x11000000, HI - 0x11000000) if LO >= 0x11800000 else (0, 0)


def ld(self, issue, addr, size, fn):
    if ALIAS[0] <= addr < ALIAS[1]:
        addr += 0x11000000
    if LO <= addr < HI:
        rd[addr][getattr(self, "_cur", 0)] += 1
    return orig_ld(self, issue, addr, size, fn)


def st(self, w, addr, size, val):
    if ALIAS[0] <= addr < ALIAS[1]:
        addr += 0x11000000
    if LO <= addr < HI:
        wr[addr][getattr(self, "_cur", 0)] += 1
        if len(vals[addr]) < 8:
            vals[addr].add(val)
    return orig_st(self, w, addr, size, val)


C.Cpu.ld, C.Cpu.st = ld, st


def tr(cpu, pc, ep):
    cpu._cur = pc


m = Machine.load(SNAP)
d = m.mem.dev.dspemu
assert type(d.cpu).__name__ == "Cpu"
d.cpu.trace = tr
if os.environ.get("MT"):
    from emu import panl
    m.mem.dev.panel_frame = panl.build_frame(keys=["MasterTempo"])
    m.cpu.run(limit=5_000_000)
    m.mem.dev.panel_frame = panl.build_frame()
for kv in filter(None, os.environ.get("SET", "").split(",")):
    a, v = kv.split("=")
    m.mem.w32(int(a, 16), int(v, 16))
m.cpu.run(limit=STEPS)
print("dsp err", d.error)
for a in sorted(set(rd) | set(wr)):
    r = ", ".join(f"{p:#010x}x{n}" for p, n in rd[a].most_common(6))
    w = ", ".join(f"{p:#010x}x{n}" for p, n in wr[a].most_common(4))
    print(f"{a:#010x}  R[{r}]  W[{w}]  vals {[hex(v) for v in sorted(vals[a])][:4]}")
