#!/usr/bin/env python3
"""dsp_split_bench.py -- where does the wall clock go during real-DSP play?

Three measurements:
  A) full cosim (same as dsp_rate_bench) with native-call timers wrapped
     around lib.core_run (SH) and lib.c6x_run (DSP) -> exact native split
  B) DSP C core alone: de._run(BIG) flat out, Mcyc/s
  C) SH alone: dev.dspemu unhitched (no DSP advance), Mcyc/s
Env: SECS=10 SNAP=cache/emu_dsp_wavplay.pkl.gz
"""
import os
import sys
import time

sys.path.insert(0, ".")

from emu.machine import Machine

SECS = float(os.environ.get("SECS", "10"))
SNAP = os.environ.get("SNAP", "cache/emu_dsp_wavplay.pkl.gz")
CPUFREQ = 500_000_000
DSPFREQ = 300_000_000


def cyc(m):
    try:
        return m.cpu.core.cycles
    except AttributeError:
        return m.cpu.cycles


def part_a():
    m = Machine.load(SNAP)
    de = m.mem.dev.dspemu
    acc = {"sh": 0.0, "dsp": 0.0, "n_sh": 0, "n_dsp": 0}

    sh_core_run = m.cpu.lib.core_run

    class ShWrap:
        def __call__(self, *a):
            t = time.perf_counter()
            r = sh_core_run(*a)
            acc["sh"] += time.perf_counter() - t
            acc["n_sh"] += 1
            return r
    m.cpu.lib.core_run = ShWrap()

    dsp_run = de.cpu.lib.c6x_run

    class DspWrap:
        def __call__(self, *a):
            t = time.perf_counter()
            r = dsp_run(*a)
            acc["dsp"] += time.perf_counter() - t
            acc["n_dsp"] += 1
            return r
    de.cpu.lib.c6x_run = DspWrap()

    de.sync()
    c0, d0 = cyc(m), de.cycles
    t0 = time.perf_counter()
    while True:
        m.cpu.run(limit=1_000_000)
        de.sync()
        if time.perf_counter() - t0 >= SECS:
            break
    dt = time.perf_counter() - t0
    c1, d1 = cyc(m), de.cycles
    glue = dt - acc["sh"] - acc["dsp"]
    print(f"A) cosim {dt:.2f}s: SH native {acc['sh']:.2f}s ({acc['n_sh']} calls), "
          f"DSP native {acc['dsp']:.2f}s ({acc['n_dsp']} calls), "
          f"Python+rest {glue:.2f}s")
    print(f"   SH {(c1 - c0) / dt / 1e6:.1f} Mcyc/s, DSP {(d1 - d0) / dt / 1e6:.1f} "
          f"Mcyc/s; DSP chunk avg {(d1 - d0) / max(1, acc['n_dsp']):.0f} cyc")
    if de.error:
        print("   DSP ERROR:", de.error)
    return m, de


def part_b(m, de):
    n = int(os.environ.get("B_CYC", "100_000_000"))
    t0 = time.perf_counter()
    de._run(n)
    dt = time.perf_counter() - t0
    print(f"B) DSP core alone: {n / dt / 1e6:.1f} Mcyc/s "
          f"({dt:.2f}s for {n / 1e6:.0f}M cyc)")
    if de.error:
        print("   DSP ERROR:", de.error)


def part_c(m, de):
    # unhitch the DSP from the SH clock: tick() will see clock=None path?
    # simplest: point dspemu.tick at nothing via hold flag (runs nothing,
    # accounts time) -- measures the SH+glue speed with DSP idle.
    de.hold = True
    de.sync = lambda n=None: None
    de.cycles0 = de.cycles
    c0 = cyc(m)
    t0 = time.perf_counter()
    while True:
        m.cpu.run(limit=1_000_000)
        if time.perf_counter() - t0 >= SECS:
            break
    dt = time.perf_counter() - t0
    print(f"C) SH alone (DSP held): {(cyc(m) - c0) / dt / 1e6:.1f} Mcyc/s")
    de.hold = False


if __name__ == "__main__":
    m, de = part_a()
    part_b(m, de)
    part_c(m, de)
