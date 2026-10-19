#!/usr/bin/env python3
"""Correlate the HPI latch timeline with the DMAC transfers to DSP RAM."""
import pickle, collections

d = pickle.load(open('/tmp/dsp_boot_probe.pkl', 'rb'))
W = d['cs6_writes']
dma = pickle.load(open('/tmp/dma_all.pkl', 'rb'))
xfer = dma['xfer']

cs6 = [x for x in xfer if (x[4] & 0x1FFFFFFF) == 0x18080000 or
                         (x[3] & 0x1FFFFFFF) == 0x18080000]

# Build latch timeline from raw CS6 writes
latch_changes = []
hpi_a = 0
for step, a, sz, v in W:
    off = a - 0x18000000
    if 0x40000 <= off < 0x40004:
        sh = (off - 0x40000) * 8
        new = (hpi_a & ~(0xFF << sh)) | ((v << sh) & 0xFFFFFFFF)
        if new != hpi_a:
            latch_changes.append((step, hpi_a, new))
        hpi_a = new

latch = 0
lc = iter(latch_changes)
next_latch = next(lc)
print("DMAC transfers to CS6 bulk FIFO, with latch state just before:")
for s, base, ch, src, dst, tcr, chcr, pc in cs6:
    while next_latch and next_latch[0] < s:
        latch = next_latch[2]
        try:
            next_latch = next(lc)
        except StopIteration:
            next_latch = None
    print(f"  step {s:>10,} ch{ch} src 0x{src:08x} tcr 0x{tcr:x} "
          f"({tcr}B) pc 0x{pc:08x}  latch_before=0x{latch:08x}")

total = sum(x[5] for x in cs6)
print(f"\ntotal bytes streamed: {total} = 0x{total:x}")

print("\nLatch timeline around the first DMAC transfer (step 332,764,981):")
for s, old, new in latch_changes:
    if 332_764_900 <= s <= 332_765_300:
        print(f"  step {s:>10,}  latch 0x{old:08x} -> 0x{new:08x}")