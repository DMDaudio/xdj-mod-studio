#!/usr/bin/env python3
"""ks_irq_watch.py -- EMULATOR ONLY, offline.

Which SH interrupt actually breaks the key-shift boot when interrupts
are left ON (the deck-alike)? Counts delivered IRQ sources per window;
the unmask wedge is owned DIFFERENTLY than the masked success, so the
name of the interrupt firing around the first divergence is the culprit
candidate to neuter.

Also dumps, per 50M-step slice, the top pending sources behind the
INTC mask so even never-delivered-but-pending culprits are visible.

Usage: .venv/bin/python tools/ks_irq_watch.py [snapshot]
"""
import os
import sys
import time
from collections import Counter
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from keyshift_reload_emu import syms, B  # noqa: E402
from emu.sh7724 import SOURCES as SH_SOURCES  # noqa: E402

SNAP = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_mounted6.pkl"
POOL_FLUSH = 0x08C92E20
RING = 0x0C8440
DECK_RING = [0x53FF7955, 0x7F797A79, 0xFF798A79, 0x79A7799D,
             0x79AA9F4B, 0xB379AEFF, 0xBA79B979, 0x79C9FF79]


def irq_setup(dev):
    """Wrap dev.take_interrupt to name each delivered source."""
    counts = Counter()
    log = []
    orig = dev.take_interrupt

    def wrap(imask=-1):
        peek = set(dev.pending)
        got = orig(imask)
        if got is not None:
            # find which source name the returned (lvl, code) was
            name = None
            best = None
            for n in peek:
                src = SH_SOURCES[n]
                # repeat the original filter: masked sources cannot deliver
                if dev._imr_masked(src[3], src[4]):
                    continue
                lvl = dev._level(n)
                if lvl == 0 or (imask != -1 and lvl <= imask):
                    continue
                cand = (lvl, src[0], n)
                if best is None or lvl > best[0]:
                    best, name = cand, n
            counts[name] += 1
            if len(log) < 2000:
                log.append((len(log), name, f"{got[0]}", f"{got[1]:#x}"))
        return got
    dev.take_interrupt = wrap
    return counts, log


def run(tag, flag, S, blob, base, max_slices=60):
    t0 = time.time()
    m = Machine.load(SNAP)
    mem, d = m.mem, m.mem.dev.dspemu
    counts, log = irq_setup(m.mem.dev)
    m.cpu.run(limit=10_000_000)
    for i, b in enumerate(blob):
        mem.w8(base + i, b)
    prev = mem.r32(POOL_FLUSH)
    mem.w32(S["ksr_prev"], prev)
    mem.w32(POOL_FLUSH, base)
    for k, v in enumerate(DECK_RING):
        mem.w32(RING + 4 * k, v)
    mem.w32(S["ksr_flag"], flag)
    st = 0
    for i in range(max_slices):
        before = sum(counts.values())
        m.cpu.run(limit=10_000_000)
        st = mem.r32(S["ksr_status"])
        delta = sum(counts.values()) - before
        top = counts.most_common(6)
        print(f"[{tag}] +{(i+1)*10}M st={st:#x} irqs_this_slice="
              f"{delta} total={sum(counts.values())} "
              f"top={top} dsp={d.cpu.pc:#x} hpic={d.hpic:#x} "
              f"pin={d.busy_pin()} hpia_dbg={mem.r32(S['ksr_dbg']+48):#x}",
              flush=True)
        if st:
            break
    dbg = mem.r32(S["ksr_dbg"] + 40)
    print(f"[{tag}] DONE st={st:#x} boot_dbg={dbg:#x} "
          f"irq_by_name={dict(counts)} [{time.time()-t0:.0f}s]", flush=True)
    if log:
        print(f"[{tag}] first 12 delivered:", flush=True)
        for row in log[:12]:
            print("   ", row)
        print(f"[{tag}] last 6 delivered:", flush=True)
        for row in log[-6:]:
            print("   ", row)
    return st, counts


def main():
    S = syms()
    blob = open(os.path.join(B, "ksr.bin"), "rb").read()
    base = S["ksr_entry"]
    print("=== MASKED (control, passes) ===", flush=True)
    s1, c1 = run("mask", 0x4B535231, S, blob, base)
    print("\n=== UNMASKED (wedge) ===", flush=True)
    s2, c2 = run("unmask", 0x4B535331, S, blob, base)
    only = {k: c2[k] for k in c2 if c1.get(k, 0) < c2[k] / 2}
    print("\nIRQS seen ONLY (or much more) in the unmask wedge:",
          {k: v for k, v in only.items() if k}, flush=True)
    print("\nIRQ totals  mask:", dict(c1))
    print("IRQ totals unmask:", dict(c2))


if __name__ == "__main__":
    sys.exit(main())
