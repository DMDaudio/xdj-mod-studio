#!/usr/bin/env python3
"""dsp_boot_stage_watch.py SNAP OUT [MASKED|UNMASKED] -- EMULATOR ONLY.

Cave-free boot rehearsal (parking-loop handoff like dsp_reboot_test) with a
per-10M-step progress line: which accessor the SH pc is in, DSP pc, HPIC,
HINT pin, the FFF0/FFF4 handshake words and the HPIA. Purpose: find exactly
which stage's wait 1) succeeds masked in ~10M steps, 2) stalls unmasked at
~950M (the deck's 0x101 without any cave).
"""
import os, sys, time, collections
sys.path[:0] = [".", "tools"]
from emu.machine import Machine

BOOT = 0x08C3777C
PARK = 0x081DFFF0
STAGES = ((0x08C374F2, 0x08C37554, "st0"), (0x08C37554, 0x08C37634, "st1"),
          (0x08C37634, 0x08C376AE, "st2"), (0x08C376AE, 0x08C3771A, "st3/4"),
          (0x08C3771A, 0x08C3777C, "st5"), (0x08C3777C, 0x08C378C0, "BOOTMAIN"))


def stage(pc):
    if not (0x08C374F2 <= pc < 0x08C378C0):
        return "other"
    for lo, hi, name in STAGES:
        if lo <= pc < hi:
            return name
    return "other"


def main():
    snap, out = sys.argv[1], sys.argv[2]
    regime = sys.argv[3].upper() if len(sys.argv) > 3 else "UNMASKED"
    t0 = time.time()
    m = Machine.load(snap)
    mem, c, d = m.mem, m.cpu, m.mem.dev.dspemu
    assert d is not None and d.running, "snapshot needs the real DSP running"
    m.cpu.run(limit=20_000_000)
    print(f"before: DSP cycles {d.cycles/1e6:.0f}M err={d.error} pc={d.cpu.pc:#x}", flush=True)
    saved = c.__getstate__()
    old_park = [mem.r16(PARK), mem.r16(PARK + 2)]
    mem.w16(PARK, 0xAFFE)
    mem.w16(PARK + 2, 0x0009)
    idle = getattr(m, "idle_skip", True)
    m.idle_skip = False
    n_ev = len(d.log)
    shbus = d.sh
    shbus.write(0x0405012E, 1, shbus.read(0x0405012E, 1) & 0xFE)
    c.pc = PARK
    m.cpu.run(limit=2_000_000)
    print(f"reset held: in_reset={d.in_reset}", flush=True)
    if regime == "MASKED":
        c.sr |= 0x10000000            # BL: block task/interrupt switching
    c.pr = PARK
    c.pc = BOOT
    ret = None
    for i in range(400):
        c.run(limit=10_000_000)
        f0 = d.bus.read(0x1183FFF0, 4)
        f4 = d.bus.read(0x1183FFF4, 4)
        hpia = (d.hpia & 0x1FFFFFF) | 0x800000
        line = (f"+{(i+1)*10}M sh={stage(c.pc):7s} dsp={d.cpu.pc:#x}/{d.cycles/1e6:.0f}M "
                f"hpic={d.hpic:#x} pin={d.busy_pin()} HPIA={hpia:#x} "
                f"FFF0={f0:#x} FFF4={f4:#x}")
        print((line + f" [{time.time()-t0:.0f}s]"), flush=True)
        if c.pc in (PARK, PARK + 2):
            ret = c.gr(0)
            break
    print(f"boot returned {ret} after ~{(i+1)*10}M steps; DSP running={d.running} "
          f"reset={d.in_reset} err={d.error} [{time.time()-t0:.0f}s]", flush=True)
    evs = d.log[n_ev:]
    print("events during call:", len(evs))
    print("   kinds:", collections.Counter(str(e[1]) if len(e) > 1 else str(e) for e in evs).most_common(12))
    saved["cycles"], saved["steps"] = c.cycles, c.steps
    c.__setstate__(saved)
    mem.w16(PARK, old_park[0]); mem.w16(PARK + 2, old_park[1])
    m.idle_skip = idle
    m.save(out)
    print("saved", out, "RESULT:", "BOOT OK" if ret == 0 else f"BOOT FAILED ret={ret}")

if __name__ == "__main__":
    main()
