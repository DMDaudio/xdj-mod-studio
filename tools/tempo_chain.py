#!/usr/bin/env python3
"""tempo_chain.py -- emu: drive payload bytes 12/13 (tempo fader -> 40D0)
and watch the FULL chain: 40D0 -> rate 3E18 -> f 2C58 -> ratio 2C50.

Settles the SH-side sign: tempo+ => ratio up or down?
Also tells the GUI fix: build_frame fader_raw must target bytes 12/13.

Run: .venv/bin/python tools/tempo_chain.py (minutes wall)
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


def r16s(m, a):
    v = int.from_bytes(bytes(m.mem.r8(a + i) for i in range(2)), "little")
    return v - 0x10000 if v >= 0x8000 else v


def raw_frame(fader12):
    f = bytearray(panl.build_frame()[:30])
    f[12] = (fader12 >> 8) & 0xFF
    f[13] = fader12 & 0xFF
    return bytes(f) + bytes((panl.checksum(bytes(f)), 0x8F))


def show(m, tag):
    print(f"{tag}: f40D0={r32(m, 0x0B6C40D0)} rate={r32(m, 0x0B6C3E18):#x} "
          f"f={r16s(m, 0x0B6C2C58)} last={r16s(m, 0x0B6C2C54)} "
          f"ratio={r32(m, 0x0B6C2C50):#x} echo={m.mem.dev.hle.echo.get(0x11837BC0, 0):#x}",
          flush=True)


def main():
    m = Machine.load("cache/emu_track_loaded.pkl")
    m.mem.dev.panel_frame = panl.build_frame()
    run(m, 1.0)
    # play + MT on
    for k in ("PlayPause", "MasterTempo"):
        m.mem.dev.panel_frame = panl.build_frame(keys=(k,))
        run(m, 0.12)
        m.mem.dev.panel_frame = panl.build_frame()
        run(m, 1.0)
    show(m, "mt-on")
    for fv in (0, 64, 128, 250, 380, 499):
        m.mem.dev.panel_frame = raw_frame(fv)
        run(m, 1.5)
        show(m, f"fader12={fv}")
    m.mem.dev.panel_frame = panl.build_frame()
    run(m, 1.0)
    show(m, "restored")


if __name__ == "__main__":
    main()
