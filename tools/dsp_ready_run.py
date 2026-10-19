#!/usr/bin/env python3
"""dsp_ready_run.py [MAX_SH_STEPS] -- boot the SH from flash with the real DSP
(XDJ_DSP_EMU=1) and print progress until the DSP's main image sets its
ready flag [0x1183FFF4] (then keep going a little to see the SH continue).
Saves cache/dsp_ready.pkl (machine) when ready."""
import os
import sys
import time
os.environ["XDJ_DSP_EMU"] = "1"
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F

MAX = int(float(sys.argv[1])) if len(sys.argv) > 1 else 6_000_000_000
flash, _ = F.load_flash(None)
m = Machine(flash)
d = m.mem.dev.dspemu
steps, t0, ready_at = 0, time.time(), None
while steps < MAX:
    m.cpu.run(limit=20_000_000)
    steps += 20_000_000
    fff4 = d.bus.read(0x1183FFF4, 4)
    fff0 = d.bus.read(0x1183FFF0, 4)
    resets = sum(1 for e in d.log if len(e) > 1 and e[1] == "reset-assert")
    print(f"SH {steps/1e6:6.0f}M pc={m.cpu.pc:#x} | DSP cyc={d.cycles/1e6:7.1f}M pc={d.cpu.pc:#x} "
          f"FFF0={fff0:#x} FFF4={fff4:#x} resets={resets} err={d.error} [{time.time()-t0:.0f}s]",
          flush=True)
    if d.error:
        break
    if fff4 and ready_at is None:
        ready_at = steps
        print("DSP READY", flush=True)
    if ready_at is not None and steps - ready_at >= int(float(os.environ.get("AFTER", "1e8"))):
        break
import collections
import pickle
seen, regions = set(), []
for lo, hi, buf in d.bus.ram:
    if id(buf) not in seen:
        seen.add(id(buf))
        regions.append((lo, bytes(buf)))
pcs = collections.Counter()
d.cpu.trace = lambda cpu, pc, ep: pcs.__setitem__(pc, pcs[pc] + 1)
d._run(300000)
d.cpu.trace = None
pickle.dump({"regions": regions, "r": list(d.cpu.r), "c": dict(d.cpu.c), "pc": d.cpu.pc,
             "mmio": list(d.bus.mmio_log), "pcs": pcs.most_common(80),
             "unknown": dict(d.bus.unknown)}, open("cache/dsp_ready.pkl", "wb"))
print("saved cache/dsp_ready.pkl; hot pcs", [(hex(a), n) for a, n in pcs.most_common(10)])
print("DSP MMIO touched after ready (top):",
      collections.Counter({(k[0], hex(k[1])): v for k, v in d.bus.unknown.items()}).most_common(15))
print("SPI1 words sent:", len(d.spi_tx), [hex(v) for _, v in d.spi_tx[:40]])
if os.environ.get("SAVE"):
    m.save(os.environ["SAVE"])
    print("machine snapshot (with the DSP) ->", os.environ["SAVE"])
import collections as _C
print("peripheral writes:", len(d.reglog))
last = _C.OrderedDict()
for cyc, name, off, v in d.reglog:
    last[(name, off)] = (v, last.get((name, off), (0, 0))[1] + 1)
for (name, off), (v, n) in sorted(last.items()):
    print(f"  {name:8s} +{off:#06x} = {v:#010x}  (x{n})")
print("DSP interrupts taken:", getattr(d.cpu, "interrupts", 0), " IFR", hex(d.cpu.c["ifr"]),
      "IER", hex(d.cpu.c["ier"]), "CSR", hex(d.cpu.c["csr"]))
print("EDMA:", d.edma.stats, "EER", hex(d.edma.eer), "IER", hex(d.edma.ier), "IPR", hex(d.edma.ipr))
print("McASP1 running", d.asp1.running(), "samples out", len(d.asp1.out),
      " McASP2 running", d.asp2.running(), "samples", len(d.asp2.out))
nz = [v for v in d.asp1.out if v]
print("McASP1 nonzero samples:", len(nz), [hex(v) for v in nz[:8]])
