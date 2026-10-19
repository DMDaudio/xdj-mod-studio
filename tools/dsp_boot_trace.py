#!/usr/bin/env python3
"""dsp_boot_trace.py [SH_STEPS] -- boot the SH from flash with the REAL DSP
(XDJ_DSP_EMU=1) and print the ordered SH<->DSP handshake (command pins,
HPIC writes, DSP events), then the DSP state; saves cache/dsp_state.pkl
(DSP RAM regions, registers, last MMIO, hottest PCs). CLAUDE.md section 81."""
import os, sys
os.environ["XDJ_DSP_EMU"] = "1"
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F
from emu import sh7724
from emu.c6x import dspdev
SEQ = []
NAMES = {0x04050128: "P128", 0x04050164: "P164", 0x0405012E: "RST", 0x0405016C: "P16C"}
orig_w = sh7724.SH7724.write
def w(self, addr, size, val):
    for i in range(size):
        k = (addr + i) & 0x1FFFFFFF
        if k in NAMES and k != 0x0405016C:
            SEQ.append(("SH", NAMES[k], hex((val >> (8 * i)) & 0xFF)))
    if (addr & 0x1FFFFFFF) == 0x18000000:
        SEQ.append(("SH", "HPIC<-", hex(val)))
    return orig_w(self, addr, size, val)
sh7724.SH7724.write = w
o_ev = dspdev.DspDevice.ev
def ev(self, *what):
    if what[0] != "hpic-w":
        SEQ.append(("DSP",) + what)
    return o_ev(self, *what)
dspdev.DspDevice.ev = ev
o_uw = dspdev.Uhpi.write
def uw(self, off, n, v):
    if off == 0x30:
        SEQ.append(("DSP", "HPIC<-", hex(v), "pc", hex(self.dev.cpu.pc)))
    return o_uw(self, off, n, v)
dspdev.Uhpi.write = uw
flash, info = F.load_flash(None)
m = Machine(flash)
steps = 0
while steps < int(float(sys.argv[1])):
    m.cpu.run(limit=10_000_000); steps += 10_000_000
# compress consecutive duplicates
out, last, n = [], None, 0
for e in SEQ:
    if e == last:
        n += 1; continue
    if last is not None: out.append((last, n))
    last, n = e, 1
out.append((last, n))
for e, n in out[:150]:
    print(("%dx " % n if n > 1 else "") + " ".join(map(str, e)))
print(len(out), "entries; SH pc", hex(m.cpu.pc))
d = m.mem.dev.dspemu
print("DSP pc", hex(d.cpu.pc), "cycles", d.cycles, "err", d.error, "running", d.running, "reset", d.in_reset)
print("FFF0/FFF4", hex(d.bus.read(0x1183FFF0, 4)), hex(d.bus.read(0x1183FFF4, 4)))
print("DDR C0000000 head", d.bus.dump(0xC0000000, 32).hex(), " C003C8C0", d.bus.dump(0xC003C8C0, 16).hex())
import collections
print("unknown MMIO top:", collections.Counter({(k[0], hex(k[1])): v for k, v in d.bus.unknown.items()}).most_common(12))
import pickle
seen = set()
regions = []
for lo, hi, buf in d.bus.ram:
    if id(buf) in seen: continue
    seen.add(id(buf)); regions.append((lo, bytes(buf)))
# PC history: run the DSP a little more with a trace of pcs
pcs = collections.Counter()
d.cpu.trace = lambda cpu, pc, ep: pcs.__setitem__(pc, pcs[pc] + 1)
d._run(200000)
d.cpu.trace = None
pickle.dump({"regions": regions, "r": list(d.cpu.r), "c": dict(d.cpu.c), "pc": d.cpu.pc,
             "mmio": list(d.bus.mmio_log), "pcs": pcs.most_common(60)},
            open("cache/dsp_state.pkl", "wb"))
print("saved; hottest pcs", [(hex(a), n) for a, n in pcs.most_common(12)])
