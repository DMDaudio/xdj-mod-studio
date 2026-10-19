#!/usr/bin/env python3
"""browse_emu.py -- drive the Browse screen in the emulator (track-load hunt).

Loads cache/emu_mainscreen.pkl (fresh boot + real stick + browse open) on
the C core and runs named steps, PNG + fb-diff after each:
  run          let it idle
  rot+ / rot-  bump the rotary counter u16 @0x0B6C7B48 (what KY codes
               0x42 RotaryPositionFwd / 0x43 Rev do: 0x8B2B1EC/0x8B2B204)
  key:NAME     hold a panl BUTTONS key for a few frames, then release
Usage: browse_emu.py STEP [STEP ...]   e.g.  rot+ rot+ key:RotaryPush
"""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

CKPT = os.environ.get("CKPT", "cache/emu_mainscreen.pkl")
ROT = 0x0B6C7B48
SLICE = int(os.environ.get("SLICE", "30000000"))


def rot(m, d):
    v = (m.mem.r16(ROT) + d) & 0xFFFF
    m.mem.w16(ROT, v)


def key(m, name, hold=3_000_000):
    m.mem.dev.panel_frame = panl.build_frame(keys=[name])
    m.cpu.run(limit=hold)
    m.mem.dev.panel_frame = panl.build_frame()


def main():
    os.makedirs("screenshots/browse", exist_ok=True)
    m = Machine.load(CKPT)
    m.mem.dev.panel_frame = panl.build_frame()
    if os.environ.get("RGB"):              # install the RGB overview mod
        import rgb_overview as R
        code, writes = R.plan(R.MODES[os.environ["RGB"]]
                              if os.environ["RGB"] in R.MODES else 1)
        for a, b in writes:            # (re)install current cave + mode
            m.mem.w8(a, b)
        print("waveform mod installed, mode", m.mem.r8(R.MODE_ADDR))
    prev = fb_png(m, "screenshots/browse/00_start.png")
    for i, step in enumerate(sys.argv[1:], 1):
        if step == "rot+":
            rot(m, 1)
        elif step == "rot-":
            rot(m, -1)
        elif step.startswith("key:"):
            key(m, step[4:])
        elif step.startswith("rot") and step[3:].lstrip("+-").isdigit():
            rot(m, int(step[3:]))
        elif step != "run":
            sys.exit(f"bad step {step}")
        m.cpu.run(limit=SLICE)
        tag = step.replace(":", "_").replace("+", "p").replace("-", "m")
        cur = fb_png(m, f"screenshots/browse/{i:02d}_{tag}.png")
        diff = sum(1 for x, y in zip(prev[::2], cur[::2]) if x != y)
        print(f"{i:02d} {step:<16} fbdiff(px)={diff:>7} rot={m.mem.r16(ROT):#06x}",
              flush=True)
        prev = cur
    if os.environ.get("SAVE"):
        m.save(os.environ["SAVE"])
        print("saved", os.environ["SAVE"])


if __name__ == "__main__":
    main()
