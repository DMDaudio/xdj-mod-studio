#!/usr/bin/env python3
"""drawwatch.py -- log every call to the popup draw fn (0x8E7D278).

Restores the 330M checkpoint, runs 10M steps with a pc watch on the
draw-function entry, capturing caller (pr) + args (r4-r7). The popup
call's args/caller differ from routine UI calls.
"""
import sys
sys.path.insert(0, ".")
from emu.machine import Machine
from emu.sh4 import PcHalt

DRAW = 0x08E7D278


def main():
    import os
    ckpt = sys.argv[1] if len(sys.argv) > 1 else "/tmp/popup_prev.pkl"
    if not os.path.exists(ckpt):
        sys.exit(f"need {ckpt} (run popup_trace.py first)")
    m = Machine.load(ckpt)
    c = m.cpu
    calls = []

    def tr(pc, op):
        if pc == DRAW and len(calls) < 25:
            calls.append((hex(c.pr),
                          [hex(c.gr(r)) for r in (4, 5, 6, 7)]))
    c.trace = tr
    c.run(limit=10_000_000)
    c.trace = None
    print(f"{len(calls)} calls to draw fn:")
    for pr, args in calls:
        print(f"  from {pr} r4-7={args}")


if __name__ == "__main__":
    main()
