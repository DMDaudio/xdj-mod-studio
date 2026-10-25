#!/usr/bin/env python3
"""dsp_rate_bench.py -- how much of real time can the real-DSP emulator
sustain, and where does the wall clock go?

Loads a PLAYING snapshot (default cache/emu_dsp_wavplay.pkl.gz), runs the
machine flat out for N wall seconds and reports:
  - SH cycles/s  (% of 500 MHz)
  - DSP cycles/s (% of 300 MHz, time-locked at 0.6)
  - McASP1 words/s (% of 88,200 words/s = 44.1 kHz stereo)
  - optional counters: CS6 read/write crossings, sync() calls, C-core runs

Env: SECS=10 SNAP=... COUNT=1 PROFILE=out.pstats
"""
import cProfile
import os
import pstats
import sys
import time

sys.path.insert(0, ".")

from emu.machine import Machine

SECS = float(os.environ.get("SECS", "10"))
SNAP = os.environ.get("SNAP", "cache/emu_dsp_wavplay.pkl.gz")
CPUFREQ = 500_000_000
DSPFREQ = 300_000_000
WORDRATE = 44100 * 2


def main():
    t0 = time.time()
    m = Machine.load(SNAP)
    print(f"loaded {SNAP} in {time.time() - t0:.1f}s")
    de = m.mem.dev.dspemu
    assert de is not None, "snapshot has no real DSP"

    def counters():
        # SH fast core keeps its own cycle counter in the C struct
        try:
            sh = m.cpu.core.cycles
        except AttributeError:
            sh = m.cpu.cycles
        return sh, de.cycles, len(de.asp1.out)

    de.sync()                                    # anchor the clock
    a0 = counters()
    t0 = time.time()
    n = 0
    prof = cProfile.Profile() if os.environ.get("PROFILE") else None
    if prof is not None:
        prof.enable()
    while True:
        m.cpu.run(limit=1_000_000)
        n += 1
        de.sync()
        if time.time() - t0 >= SECS:
            break
    if prof is not None:
        prof.disable()
        prof.dump_stats(os.environ["PROFILE"])
    dt = time.time() - t0
    a1 = counters()

    shr = (a1[0] - a1[0] * 0 + (a1[0] - a0[0])) / dt
    dspr = (a1[1] - a0[1]) / dt
    wr = (a1[2] - a0[2]) / dt
    print(f"wall {dt:.2f}s  ({n} slices of 1M)")
    print(f"SH : {(a1[0] - a0[0]) / dt / 1e6:8.2f} Mcyc/s "
          f"({(a1[0] - a0[0]) / dt / CPUFREQ * 100:5.1f}% of 500 MHz)")
    print(f"DSP: {(a1[1] - a0[1]) / dt / 1e6:8.2f} Mcyc/s "
          f"({(a1[1] - a0[1]) / dt / DSPFREQ * 100:5.1f}% of 300 MHz)")
    print(f"AUD: {wr:8.0f} words/s ({wr / WORDRATE * 100:5.1f}% of real-time "
          f"44.1k stereo)")
    if de.error:
        print(f"DSP ERROR: {de.error}")
    if os.environ.get("COUNT"):
        print(f"cs6 reads={getattr(de, '_n_rd', '?')} writes={getattr(de, '_n_wr', '?')}")


if __name__ == "__main__":
    main()
