#!/usr/bin/env python3
"""fader_sweep.py -- emu: which panel payload bytes drive 0x0B6C2C58 (f)?

f = the signed short the MT ratio formula consumes
  (ratio = 1245184 * (1 + f/10000) -> 0x0B6C2C50; FUN_08d36260).
Something in the emu zeroes direct writes to 2C58 every frame, so the
producer runs live. Sweep each analog byte position with a distinctive
value and watch 2C58 (+40D0, +struct mirrors, +ratio).

Run: .venv/bin/python tools/fader_sweep.py (minutes wall)
"""
import os
import sys

os.environ["XDJ_HLE_DSP"] = "1"
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl

CPUFREQ = 500_000_000


def run(m, s):
    m.cpu.run(limit=int(s * CPUFREQ))


def r32(m, a):
    return int.from_bytes(bytes(m.mem.r8(a + i) for i in range(4)), "little")


def r16(m, a):
    return int.from_bytes(bytes(m.mem.r8(a + i) for i in range(2)), "little")


def raw_frame(mods):
    f = bytearray(panl.build_frame())
    f = bytearray(f[:30])
    for off, val in mods:
        f[off] = val & 0xFF
    f.append(panl.checksum(bytes(f)))
    f.append(0x8F)
    return bytes(f)


def main():
    m = Machine.load("cache/emu_track_loaded.pkl")
    m.mem.dev.panel_frame = panl.build_frame()
    run(m, 1.0)
    print(f"baseline: f2C58={r16(m, 0x0B6C2C58)} "
          f"f40D0={r32(m, 0x0B6C40D0)} ratio={r32(m, 0x0B6C2C50):#x} "
          f"struct64={r32(m, 0x0B6C7A64):#x} struct68={r32(m, 0x0B6C7A68):#x} "
          f"struct6C={r32(m, 0x0B6C7A6C):#x} struct70={r32(m, 0x0B6C7A70):#x}",
          flush=True)
    # single-byte probes first (byte 13 is the known 40D0 driver)
    for off in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14):
        m.mem.dev.panel_frame = raw_frame([(off, 0xAA)])
        run(m, 0.3)
        print(f"byte{off}=0xAA: f2C58={r16(m, 0x0B6C2C58)} "
              f"f40D0={r32(m, 0x0B6C40D0)} ratio={r32(m, 0x0B6C2C50):#x} "
              f"s64={r32(m, 0x0B6C7A64):#x} s68={r32(m, 0x0B6C7A68):#x} "
              f"s6C={r32(m, 0x0B6C7A6C):#x} s70={r32(m, 0x0B6C7A70):#x}",
              flush=True)
    # BE-pair probes at the parser's analog slots
    for off in (4, 6, 7, 8, 9, 10, 11, 12):
        m.mem.dev.panel_frame = raw_frame([(off, 0x01), (off + 1, 0x00)])
        run(m, 0.3)
        print(f"pair{off}=0x0100: f2C58={r16(m, 0x0B6C2C58)} "
              f"f40D0={r32(m, 0x0B6C40D0)} ratio={r32(m, 0x0B6C2C50):#x}",
              flush=True)


if __name__ == "__main__":
    main()
