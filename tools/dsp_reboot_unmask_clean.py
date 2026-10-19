#!/usr/bin/env python3
"""dsp_reboot_unmask_clean.py SNAP OUT -- EMULATOR ONLY.

Same handoff as tools/dsp_reboot_test.py (parking loop, pc=BOOT) but WITHOUT
SR |= BL: interrupts keep flowing during the boot, i.e. the deck-alike
UNMASKED regime in a CAVE-FREE context. 0x101/wedge here = unmask wedges in
general; BOOT OK = the wedge needs the cave's install, not just the mask.
"""
import sys, time, collections
sys.path[:0] = [".", "tools"]
from emu.machine import Machine

BOOT = 0x08C3777C
PARK = 0x081DFFF0

def main():
    snap, out = sys.argv[1], sys.argv[2]
    t0 = time.time()
    m = Machine.load(snap)
    mem, c, d = m.mem, m.cpu, m.mem.dev.dspemu
    assert d is not None and d.running, "snapshot needs the real DSP running"
    m.cpu.run(limit=20_000_000)
    print(f"before: DSP cycles {d.cycles/1e6:.0f}M err={d.error} pc={d.cpu.pc:#x}", flush=True)
    saved = c.__getstate__()
    old_park = [mem.r16(PARK), mem.r16(PARK + 2)]
    mem.w16(PARK, 0xAFFE)                 # bra .
    mem.w16(PARK + 2, 0x0009)             # nop
    idle = getattr(m, "idle_skip", True)
    m.idle_skip = False
    n_ev = len(d.log)
    if True:                              # hold reset like power-on
        sh = d.sh
        sh.write(0x0405012E, 1, sh.read(0x0405012E, 1) & 0xFE)
        c.pc = PARK
        m.idle_skip = False
        m.cpu.run(limit=2_000_000)
        print(f"reset held: in_reset={d.in_reset}", flush=True)
    # NO SR BL here: the unmasked regime under test
    c.pr = PARK
    c.pc = BOOT
    ret = None
    for i in range(400):                  # up to 4e9 SH steps
        c.run(limit=10_000_000)
        if c.pc in (PARK, PARK + 2):
            ret = c.gr(0)
            break
    print(f"boot returned {ret} after ~{(i+1)*10}M steps; DSP running={d.running} "
          f"reset={d.in_reset} err={d.error} [{time.time()-t0:.0f}s]", flush=True)
    evs = d.log[n_ev:]
    print("events during call:", len(evs))
    for e in evs[:25]: print("   ", e)
    print("   kinds:", collections.Counter(str(e[1]) if len(e) > 1 else str(e) for e in evs).most_common(12))
    saved["cycles"], saved["steps"] = c.cycles, c.steps
    c.__setstate__(saved)
    mem.w16(PARK, old_park[0]); mem.w16(PARK + 2, old_park[1])
    m.idle_skip = idle
    m.save(out)
    print("saved", out, "RESULT:", "BOOT OK" if ret == 0 else f"BOOT FAILED ret={ret}")

if __name__ == "__main__":
    main()
