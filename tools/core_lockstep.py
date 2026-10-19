#!/usr/bin/env python3
"""core_lockstep.py CHECKPOINT [STEPS]

Runs the C core (emu/fastcore.c) and the Python core (emu/sh4.py) side by
side from the same checkpoint, one instruction at a time, and compares all
CPU state after every instruction. Device time is frozen (no ticks) and the
Python core's native shortcuts are off, so any difference is a CPU bug.
"""
import struct
import sys
import time

sys.path.insert(0, ".")
from emu.machine import Machine


def f2u(x):
    try:
        return struct.unpack("<I", struct.pack("<f", x))[0]
    except OverflowError:
        return 0x7F800000 if x > 0 else 0xFF800000


def snap(c):
    d = c.__getstate__()
    regs = {k: d[k] for k in ("pc", "pr", "gbr", "vbr", "mach", "macl", "sr",
                              "ssr", "spc", "sgr", "fpscr", "fpul", "tea",
                              "expevt")}
    regs["r"] = [c.gr(i) for i in range(16)]
    regs["b0"], regs["b1"] = list(d["b0"]), list(d["b1"])
    regs["fr"] = d.get("fr_raw") or [f2u(v) for b in d["fr"] for v in b]
    regs["sleeping"] = bool(d["sleeping"])
    return regs


def main():
    path = sys.argv[1]
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 200000
    mp = Machine.load(path, core="py")
    mf = Machine.load(path, core="fast")
    assert type(mf.cpu).__name__ == "FastSH4"
    for m in (mp, mf):
        m.idle_skip = False
        m._real_tick = m.mem.dev.tick
        m.mem.tick = lambda cyc: None       # ticks are driven below, in step
    mp.cpu.native_hook = None
    t = time.time()
    pcs = set()
    for i in range(steps):
        mp.cpu.run(limit=1)
        mf.cpu.run(limit=1)
        pcs.add(mp.cpu.pc)
        if (i & 0xFF) == 0xFF:              # same device time on both sides
            mp._real_tick(256)
            mf._real_tick(256)
        a, b = snap(mp.cpu), snap(mf.cpu)
        if a != b:
            print(f"DIVERGED after {i + 1} instructions")
            for k in a:
                if a[k] != b[k]:
                    if isinstance(a[k], list):
                        diffs = [(j, hex(x), hex(y)) for j, (x, y) in
                                 enumerate(zip(a[k], b[k])) if x != y]
                        print(f"  {k}: py/c differ at {diffs}")
                    else:
                        print(f"  {k}: py={a[k]:#x} c={b[k]:#x}")
            print("  last python-core pcs:",
                  [hex(p) for p, _ in list(mp.trace)[-8:]])
            return 1
    fb = mp.mem.fb[0] == mf.mem.fb[0] and mp.mem.fb[1] == mf.mem.fb[1]
    r0 = mp.mem.ram0 == mf.mem.ram0 and mp.mem.ram1 == mf.mem.ram1
    print(f"{steps:,} instructions identical in {time.time() - t:.0f}s; "
          f"RAM identical: {r0}, framebuffers identical: {fb}; "
          f"exceptions py={mp.exc_total} c={mf.exc_total}; "
          f"{len(pcs):,} distinct PCs, interrupts taken: "
          f"{mp.mem.dev.cyc and 'yes'}")
    return 0 if (r0 and fb) else 1


if __name__ == "__main__":
    sys.exit(main())
