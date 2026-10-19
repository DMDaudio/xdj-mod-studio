#!/usr/bin/env python3
"""diag_latch.py -- PP_GUARD + incremental paint: watch the pblit latch,
the BJ stage byte (BJNEXT) and CPU exceptions across BEAT JUMP paints.
Find out why the latch stays set in guard_emu_test after the BJ period."""
import os
import sys

sys.path[:0] = [".", "tools"]
import perform_pads as PP                   # noqa: E402
from emu.machine import Machine             # noqa: E402
from emu import panl                        # noqa: E402

SNAP = os.environ.get("SNAP", "cache/emu_dsp_idle.pkl")
m = Machine.load(SNAP)
mem, dev, c = m.mem, m.mem.dev, m.cpu
print("installing pads", flush=True)
PP.install(mem)

before = mem.exc_total if hasattr(mem, "exc_total") else -1
print("exceptions before:", before, flush=True)


def run(n):
    for _ in range(int(n) // 500_000):
        c.run(limit=500_000)


run(50e6)
dev.panel_frame = panl.build_frame(touch=(710, 38))     # PERFORM
run(10e6)
dev.panel_frame = panl.build_frame()
for _ in range(120):
    run(500_000)
    if mem.r32(PP.ZSTATE) == 0x1E:
        break
dev.panel_frame = panl.build_frame(touch=(PP.TAB_X + PP.TAB_W // 2,
                                          PP.TAB_Y[1] + PP.TAB_H // 2))
run(3_000_000)
dev.panel_frame = panl.build_frame()
print("BJ tap: mode", mem.r8(PP.PST + 2), "busy", mem.r8(PP.PBUSY),
      "stage", mem.r8(PP.BJNEXT), flush=True)
for i in range(16):
    run(2_000_000)
    print(f"  +{(i + 1) * 2}M busy={mem.r8(PP.PBUSY)} "
          f"stage={mem.r8(PP.BJNEXT)} "
          f"exc={getattr(mem, 'exc_total', '?')} "
          f"(was {before})", flush=True)
print("DONE", flush=True)
