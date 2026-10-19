#!/usr/bin/env python3
"""dsp_reboot_test.py SNAP OUT -- EMULATOR ONLY. Can the XDJ-700 re-boot its
audio chip (DSP) in the middle of a session and keep working?

Key shift "option 1" (safe route) = patch the DSP program copy in SH RAM and
make the deck reload the DSP from it. The deck itself boots the DSP exactly
once, very early at power-on (FUN_08C3777C, called from the startup code at
0x080008EA; returns 0 = ok, 1 = failed after 3 tries). This test calls that
same routine from a running snapshot (stick mounted, real DSP), hands the
CPU back, and saves the result so tools/dsp_play_test.py can try to load and
play a track afterwards.

The call: SH registers saved, interrupts blocked (SR.BL, like the early
startup context), pr = a parking loop written into scratch RAM, pc = the
boot routine; the C core runs until pc reaches the parking loop; registers
restored. Nothing here touches a real deck.
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402

BOOT = 0x08C3777C
PARK = 0x081DFFF0          # emulator scratch: "bra . / nop" parking loop
BL = 0x10000000


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
    if os.environ.get('HOLD_RESET', '1') == '1':   # power-on state: DSP in reset
        sh = d.sh                               # hold reset with tasks paused
        sh.write(0x0405012E, 1, sh.read(0x0405012E, 1) & 0xFE)
        c.sr |= BL
        c.pc = PARK
        m.idle_skip = False
        mem.w16(PARK, 0xAFFE)
        mem.w16(PARK + 2, 0x0009)
        m.cpu.run(limit=2_000_000)
        print(f'reset held: in_reset={d.in_reset}', flush=True)
    c.sr |= BL
    c.pr = PARK
    c.pc = BOOT
    ret = None
    for i in range(400):                  # up to 4e9 SH steps
        c.run(limit=10_000_000)
        if c.pc in (PARK, PARK + 2):
            ret = c.gr(0)
            break
    print(f"boot routine returned {ret} after ~{(i+1)*10}M steps; "
          f"DSP running={d.running} reset={d.in_reset} err={d.error} "
          f"[{time.time()-t0:.0f}s]", flush=True)
    import collections
    evs = d.log[n_ev:]
    print('events during call:', len(evs))
    for e in evs[:60]: print('   ', e)
    print('   kinds:', collections.Counter(str(e[1]) if len(e)>1 else str(e) for e in evs).most_common(20))
    saved['cycles'], saved['steps'] = c.cycles, c.steps   # time keeps going
    c.__setstate__(saved)
    mem.w16(PARK, old_park[0])
    mem.w16(PARK + 2, old_park[1])
    m.idle_skip = idle
    for k in range(5):
        m.cpu.run(limit=100_000_000)
        print(f"  after +{(k+1)*100}M: DSP cycles {d.cycles/1e6:.0f}M err={d.error} "
              f"pc={d.cpu.pc:#x} [{time.time()-t0:.0f}s]", flush=True)
    m.save(out)
    print("saved", out, "RESULT:", "BOOT OK" if ret == 0 else "BOOT FAILED")


if __name__ == "__main__":
    main()
