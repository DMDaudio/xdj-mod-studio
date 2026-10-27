#!/usr/bin/env python3
"""io_profile.py [SNAP] [STEPS] -- which device accesses still cross from
the C SH core into Python (fastcpu _io_rd/_io_wr/_io_rds/_io_wrs), per 4 KB
page: count and Python time, plus the run's wall split. Guides what to
move into C next for emulator speed."""
import collections
import sys
import time
sys.path.insert(0, ".")
from emu.machine import Machine


def main():
    snap = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_playing7.pkl"
    steps = int(float(sys.argv[2])) if len(sys.argv) > 2 else 100_000_000
    from emu import fastcpu
    cnt = collections.Counter()
    tim = collections.Counter()
    for name in ("_io_rd", "_io_wr", "_io_rds", "_io_wrs"):   # before the
        orig = getattr(fastcpu.FastSH4, name)                  # callbacks bind

        def wrap(*a, _o=orig, _n=name):
            t = time.perf_counter()
            r = _o(*a)
            k = (_n, (a[1] & 0xFFFFFFFF) >> 12)
            cnt[k] += 1
            tim[k] += time.perf_counter() - t
            return r
        setattr(fastcpu.FastSH4, name, wrap)
    m = Machine.load(snap)
    cpu = m.cpu
    t0 = time.perf_counter()
    cpu.run(limit=steps)
    wall = time.perf_counter() - t0
    tot = sum(tim.values())
    print(f"{steps / 1e6:.0f}M steps in {wall:.1f}s, io python {tot:.1f}s ({100 * tot / wall:.0f}%), "
          f"{sum(cnt.values()):,} crossings")
    for k, n in sorted(cnt.items(), key=lambda kv: -tim[kv[0]])[:15]:
        print(f"  {k[0]:8s} {k[1] << 12:#010x}  {n:>9,}  {tim[k]:6.2f}s")


if __name__ == "__main__":
    main()
