#!/usr/bin/env python3
"""perf_probe.py -- measure the SH-4 core's speed and hotspots.

Modes (env/flags):
  ... bench SECS      run a playing/idle snapshot for SECS wall, report
                      steps/s and emulated-seconds-per-wall-second (SH @ 500 MHz)
  ... profile STEPS   cProfile N steps of a snapshot, top functions

Usage:
  .venv/bin/python tools/perf_probe.py bench cache/emu_dsp_wavplay.pkl.gz 5
"""
import cProfile
import os
import pstats
import sys
import time

sys.path.insert(0, ".")
from emu.machine import Machine

SH_HZ = 500_000_000


def load(snap):
    return Machine.load(snap)             # handles both .pkl and .pkl.gz


def run_bench(m, secs):
    cpu = m.cpu
    start = cpu.steps
    t0 = time.time()
    while time.time() - t0 < secs:
        cpu.run(limit=2_000_000)
    t1 = time.time()
    return cpu.steps - start, t1 - t0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "bench"
    snap = sys.argv[2] if len(sys.argv) > 2 else "cache/emu_dsp_wavplay.pkl.gz"
    arg = sys.argv[3] if len(sys.argv) > 3 else None
    m = load(snap)
    print(f"{snap}: core={type(m.cpu).__name__} steps@load={m.cpu.steps:,}")
    if mode == "bench":
        secs = float(arg or 5)
        warm = run_bench(m, 0.5)               # warm caches/decode
        steps, wall = run_bench(m, secs)
        print(f"steps {steps:,} in {wall:.2f}s -> {steps/wall/1e6:.1f} M steps/s")
        print(f"emulated time {steps/SH_HZ:.2f}s / {wall:.2f}s wall "
              f"= {steps/SH_HZ/wall*100:.1f}% of real time")
    elif mode == "profile":
        steps = int(arg or 20_000_000)
        prof = cProfile.Profile()
        prof.enable()
        m.cpu.run(limit=steps)
        prof.disable()
        st = pstats.Stats(prof)
        st.sort_stats("tottime")
        st.print_stats(25)
    elif mode == "cbcount":
        secs = float(arg or 5)
        mem = m.mem
        cnt = {"rd": 0, "wr": 0, "rds": 0, "wrs": 0}
        cpu = m.cpu
        if hasattr(cpu, "_cb_rd"):
            import emu.fastcpu as fc
            # count real crossings by swapping in shim callbacks: too costly.
        warm = run_bench(m, 0.5)
        steps, wall = run_bench(m, secs)
        print(f"steps {steps:,} ({steps/wall/1e6:.1f} M/s)")


if __name__ == "__main__":
    main()
