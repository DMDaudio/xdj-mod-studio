#!/usr/bin/env python3
"""guard_emu_test.py -- PP_GUARD build: install the pads with the new
pblit re-entrancy latch, open PERFORM, tap the BEAT JUMP tab (the deck
freeze trigger) and a pad; verify the row paints, interactions work and
the latch (PBUSY) always returns to 0. Also samples the SH4 stack
pointer minimum during the BJ paint (stack-depth data for free).
"""
import os
import sys

sys.path[:0] = [".", "tools"]
import perform_pads as PP                   # noqa: E402
from emu.machine import Machine             # noqa: E402
from emu import panl                        # noqa: E402
from ghosttap_emu import fb_png             # noqa: E402

SNAP = os.environ.get("SNAP", "cache/emu_dsp_idle.pkl")
m = Machine.load(SNAP)
mem, dev, c = m.mem, m.mem.dev, m.cpu
print("installing pads with the PP_GUARD latch", flush=True)
PP.install(mem)

r15_min = [1 << 30]


def sample():
    try:
        r = getattr(c, "r", None)
        if r is not None:
            r15_min[0] = min(r15_min[0], r[15])
    except Exception:
        pass


def run(n):
    for _ in range(int(n) // 500_000):
        c.run(limit=500_000)
        sample()


def touch(p, hold=3_000_000, after=3_000_000):
    for _ in range(60):
        if mem.r32(PP.ZSTATE) == 0x1E:
            break
        run(500_000)
    dev.panel_frame = panl.build_frame(touch=p)
    run(hold)
    dev.panel_frame = panl.build_frame()
    run(after)
    for _ in range(60):
        if mem.r8(PP.PST) == 0 and mem.r32(PP.ZSTATE) == 0x1E:
            break
        run(500_000)


run(50e6)
touch((710, 38))                             # PERFORM top-row button
print("PERFORM open:", mem.r32(0x0BA371D4) == 7, "busy=", mem.r8(PP.PBUSY),
      flush=True)
fb_png(m, "screenshots/guard_perform.png")
touch((PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[1] + PP.TAB_H // 2))   # BEAT JUMP
print("BJ tab: mode byte", mem.r8(PP.PST + 2), "busy=", mem.r8(PP.PBUSY),
      flush=True)
run(200e6)                                   # many paints with the row live
fb_png(m, "screenshots/guard_bj.png")
busy = mem.r8(PP.PBUSY)
for _ in range(400):                         # a slice boundary can cut inside
    busy = mem.r8(PP.PBUSY)                  # pblit mid-flight; step on until
    if busy == 0:                            # a sample lands in an idle gap
        break
    c.run(limit=50_000)
print("after 200M: busy=", busy, "(0 = latch released)", flush=True)
# a beat jump pad tap (pad 1: first pair) -- pads row y from items table
it = PP.PAD_ITEMS if hasattr(PP, "PAD_ITEMS") else None
touch((40, 232 + 40))                        # inside the row area
run(100e6)
fb_png(m, "screenshots/guard_bjpad.png")
print("after pad tap: busy=", mem.r8(PP.PBUSY),
      "latch=", mem.r8(PP.PST), flush=True)
# back to KEY SHIFT + HOT CUE tabs (regression: other modes still paint)
touch((PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[2] + PP.TAB_H // 2))
print("KS tab: mode", mem.r8(PP.PST + 2), "busy=", mem.r8(PP.PBUSY), flush=True)
run(100e6)
touch((PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[0] + PP.TAB_H // 2))
print("HC tab: mode", mem.r8(PP.PST + 2), "busy=", mem.r8(PP.PBUSY), flush=True)
run(100e6)
fb_png(m, "screenshots/guard_hc.png")
print(f"r15 min seen: {r15_min[0]:#x}", flush=True)
print("RESULT:", "PASS" if busy == 0 else "FAIL (latch stuck)", flush=True)
