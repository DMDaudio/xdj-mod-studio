#!/usr/bin/env python3
"""unaligned_check.py -- the real SH-4 raises an address error on a 16/32-bit
access at an unaligned address; the emulator silently allows it. Taps each
PERFORM tab (pads only) on the py core and lists every unaligned access with
the pc that made it. 2026-10-12: the STEMS tab hard-crashed the real deck."""
import collections
import os
import sys
sys.path[:0] = [".", "tools"]
from emu import sh4, panl                     # noqa: E402
from emu.machine import Machine               # noqa: E402
import perform_pads as PP                     # noqa: E402

BAD = collections.Counter()
FIRST = {}
for name in ("_rd", "_wr"):
    orig = getattr(sh4.SH4, name)

    def make(orig, name):
        def f(self, a, n, *v, **kw):
            if n > 1 and a % n:
                k = (name, hex(self.pc), n, hex(a))
                BAD[(name, hex(self.pc), n)] += 1
                FIRST.setdefault((name, hex(self.pc), n), hex(a))
            return orig(self, a, n, *v, **kw)
        return f
    setattr(sh4.SH4, name, make(orig, name))

m = Machine.load("cache/emu_perform_track.pkl", core="py")
PP.install(m.mem)
c = m.cpu


def run(n, frame=None):
    m.mem.dev.panel_frame = frame or panl.build_frame()
    c.run(limit=int(n))


run(6e6)
TAB = lambda k: (PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[k] + PP.TAB_H // 2)
for k, name in ((1, "BEAT JUMP"), (2, "KEY SHIFT"), (3, "STEMS")):
    BAD.clear()
    run(3e6, panl.build_frame(touch=TAB(k)))
    run(8e6)
    ours = {k_: v for k_, v in BAD.items() if 0x081D0000 <= int(k_[1], 16) < 0x081E8000}
    print(f"{name}: unaligned accesses total {sum(BAD.values())}, from our code {sum(ours.values())}")
    for k_, v in sorted(ours.items(), key=lambda x: -x[1])[:8]:
        print("   ", k_, "x", v, "first addr", FIRST[k_])
