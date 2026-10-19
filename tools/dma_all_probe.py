#!/usr/bin/env python3
"""Log EVERY DMAC transfer during boot, looking for any that targets the CS6
bulk FIFO (0x18080000) or its P2 alias (0xB8080000) -- the DSP image download.
"""
import os, sys, pickle
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, ".."))
from emu.machine import Machine
from emu import fw as F

STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 400_000_000
flash, _ = F.load_flash(None)
m = Machine(flash)
dev = m.mem.dev

xfer = []
_orig = dev._dma_run
def hook(base, ch):
    st = dev.dma[base]["ch"][ch]
    pre = (st["sar"], st["dar"], st["tcr"], st["chcr"], m.cpu.pc)
    _orig(base, ch)
    xfer.append((m.cpu.steps, base, ch) + pre)
dev._dma_run = hook

print(f"booting {STEPS:,} ...", flush=True)
m.cpu.run(limit=STEPS)

# Filter: any transfer whose DAR (or SAR) touches CS6 bulk FIFO or DSP RAM
def touches_cs6(a):
    a &= 0x1FFFFFFF
    return 0x18080000 <= a < 0x18080100
def touches_dspram(a):
    a &= 0x1FFFFFFF
    return 0x11800000 <= a < 0x11840000

cs6_x = [x for x in xfer if touches_cs6(x[4]) or touches_cs6(x[3])]
dsp_x = [x for x in xfer if touches_dspram(x[4]) or touches_dspram(x[3])]
print(f"\ntotal DMAC transfers: {len(xfer)}")
print(f"transfers touching CS6 bulk FIFO: {len(cs6_x)}")
print(f"transfers touching DSP RAM (0x11800000-0x11840000): {len(dsp_x)}")
for s, base, ch, src, dst, tcr, chcr, pc in dsp_x[:40]:
    print(f"  step {s:>10,} ch{ch} base 0x{base:08x} src 0x{src:08x} "
          f"dst 0x{dst:08x} tcr 0x{tcr:x} chcr 0x{chcr:08x} pc 0x{pc:08x}")

# Also: what are the most common DAR values overall?
import collections
dc = collections.Counter(x[4] & 0x1FFFFFFF for x in xfer)
print("\nmost common DAR values (physical):")
for v, n in dc.most_common(15):
    print(f"  0x{v:08x}: {n}")

pickle.dump({"xfer": xfer, "steps": m.cpu.steps, "pc": m.cpu.pc},
            open('/tmp/dma_all.pkl', 'wb'))
print("saved /tmp/dma_all.pkl")