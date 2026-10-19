#!/usr/bin/env python3
"""dsp_boot_probe.py -- capture the REAL DSP image upload during boot.

The firmware streams the DSP program word-by-word through the CS6/HPI bridge:
HPIA (0x18040000) latches a DSP address, then HPID (0x180C0000) writes the
value. Both go through Python device callbacks even under the C core, so we
hook them and record every (dsp_addr, value) word.

    .venv/bin/python tools/dsp_boot_probe.py [steps]

Default: 600M steps.
"""
import os, sys, pickle, collections
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, ".."))
from emu.machine import Machine
from emu import fw as F

STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 600_000_000

flash, _ = F.load_flash(None)
m = Machine(flash)
dev = m.mem.dev

cs6_writes = []      # (step, addr, size, val)  raw CS6 writes (latch + data)
words = []           # (dsp_addr, value)        completed HPID words

# --- record every raw CS6 write (both latch and data port) ---
_orig_cs6_write = dev._cs6_write
def w(a, size, val):
    _orig_cs6_write(a, size, val)
    cs6_writes.append((m.cpu.steps, a, size, val))
dev._cs6_write = w

print(f"booting {STEPS:,} steps ...", flush=True)
m.cpu.run(limit=STEPS)

# Collapse every CS6 write (latch + data port, in order) into 32-bit HPID
# words keyed by the latched DSP address.
hpi_a = 0
acc = 0
cnt = 0
for step, a, sz, v in cs6_writes:
    off = a - 0x18000000
    if 0x40000 <= off < 0x40004:    # HPIA latch (0x18040000-3)
        sh = (off - 0x40000) * 8
        hpi_a = (hpi_a & ~(0xFF << sh)) | ((v << sh) & 0xFFFFFFFF)
    elif 0xc0000 <= off < 0xc0004:  # HPID data port (0x180c0000-3)
        sh = (off - 0xc0000) * 8
        if cnt == 0:
            acc = 0
        acc |= (v & ((1 << (sz * 8)) - 1)) << sh
        cnt += sz
        if cnt == 4:
            words.append((hpi_a, acc))
            cnt = 0

print(f"\nHPID word writes: {len(words)}")

addrs = collections.Counter(a for a, _ in words)
print(f"unique DSP addresses written: {len(addrs)}")
if addrs:
    lo = min(addrs); hi = max(addrs)
    print(f"DSP address range: 0x{lo:08x} - 0x{hi:08x}  (span 0x{hi - lo:x})")

    bins = collections.Counter()
    for a in addrs:
        bins[a & ~0xFFF] += 1
    print("\nper-0x1000-page write counts (top 30):")
    for base, n in bins.most_common(30):
        print(f"  0x{base:08x}: {n}")

    runs = []
    prev = None; start = None; cnt2 = 0
    for a in sorted(addrs):
        if prev is not None and a == prev + 4:
            cnt2 += 1
        else:
            if start is not None and cnt2 >= 8:
                runs.append((start, prev, cnt2))
            start = a; cnt2 = 1
        prev = a
    if start is not None and cnt2 >= 8:
        runs.append((start, prev, cnt2))
    print(f"\ncontiguous 4-byte-strided runs (>=8 words): {len(runs)}")
    for s, e, n in runs[:40]:
        print(f"  0x{s:08x} - 0x{e:08x}  ({n} words, {n * 4} bytes)")

    vals = [v for _, v in words]
    def classify(v):
        if 0x08000000 <= v <= 0x0C000000: return 'SH-RAM'
        if 0x0E000000 <= v <= 0x0F000000: return 'SH-periph'
        if 0x11800000 <= v <= 0x11840000: return 'DSP-RAM'
        if v < 0x01000000: return 'low'
        return 'other'
    print(f"\nHPID value classes: {dict(collections.Counter(classify(v) for v in vals))}")

    print("\nfirst 25 HPID writes:")
    for a, v in words[:25]:
        print(f"  dsp 0x{a:08x}  val 0x{v:08x}")
    print("\nlast 10 HPID writes:")
    for a, v in words[-10:]:
        print(f"  dsp 0x{a:08x}  val 0x{v:08x}")

print(f"\nfinal pc 0x{m.cpu.pc:08x} steps {m.cpu.steps:,} cs6={dev.cs6}")

pickle.dump({"words": words, "cs6_writes": cs6_writes,
             "steps": m.cpu.steps, "pc": m.cpu.pc, "cs6": dev.cs6},
            open("/tmp/dsp_boot_probe.pkl", "wb"))
print("saved /tmp/dsp_boot_probe.pkl")