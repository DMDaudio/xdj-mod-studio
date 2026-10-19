#!/usr/bin/env python3
"""ratio_sign_test.py -- emu: does the SH ratio follow the fader formula?

Static result (Ghidra FUN_08d36260 + pool dump): the ratio writers store
  ratio = 1245184 * (1 + f/10000),  f = signed short @0x0B6C2C58
into 0x0B6C2C50. This test drives f live (MT on, playing) and watches 2C50:
  CONFIRMED + sign  => direction prior for the DSP convention question.
  no move           => the module mode gate blocks; try the natural fader path.

Run: .venv/bin/python tools/ratio_sign_test.py (~5 s emulated, minutes wall)
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


def frame(m, keys=(), fader=250):
    m.mem.dev.panel_frame = panl.build_frame(keys=keys, fader_raw=fader)


def press(m, key, hold=0.12, settle=0.9):
    frame(m, (key,))
    run(m, hold)
    frame(m, ())
    run(m, settle)


def r32(m, a):
    return int.from_bytes(bytes(m.mem.r8(a + i) for i in range(4)), "little")


def r16(m, a):
    return int.from_bytes(bytes(m.mem.r8(a + i) for i in range(2)), "little")


def w16(m, a, v):
    for i, b in enumerate(v.to_bytes(2, "little", signed=True)):
        m.mem.w8(a + i, b)


def w32(m, a, v):
    for i, b in enumerate(v.to_bytes(4, "little")):
        m.mem.w8(a + i, b)


def show(m, tag):
    print(f"{tag}: mode={r32(m, 0x0B6C4264)} MT={r32(m, 0x0B6C4308)} "
          f"modemode={r32(m, 0x0B6C2C4C)} dirty={r32(m, 0x0B6C2C48)} "
          f"ratio={r32(m, 0x0B6C2C50):#x} last={r16(m, 0x0B6C2C54)} "
          f"f={r16(m, 0x0B6C2C58)}-as-u16",
          flush=True)


def main():
    m = Machine.load("cache/emu_track_loaded.pkl")
    frame(m)
    run(m, 1.0)
    show(m, "idle")
    press(m, "PlayPause")
    run(m, 1.2)
    show(m, "playing")
    press(m, "MasterTempo")
    run(m, 1.2)
    show(m, "mt-on")

    # natural path: move the panel fader, see if the short follows
    frame(m, (), fader=175)
    run(m, 1.5)
    show(m, "fader-frame-175")

    # direct path: force the formula input + dirty, watch the ratio
    for f in (500, -500):
        w16(m, 0x0B6C2C58, f)
        w32(m, 0x0B6C2C48, 1)
        run(m, 1.0)
        got = r32(m, 0x0B6C2C50)
        exp = int(1245184 * (1 + f / 10000))
        print(f"forced f={f:+d}: ratio={got:#x} ({got}), "
              f"formula predicts {exp:#x} ({exp})",
              flush=True)
    show(m, "final")


if __name__ == "__main__":
    main()
