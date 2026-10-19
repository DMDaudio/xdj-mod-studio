#!/usr/bin/env python3
"""popup_trace.py -- find the first firmware write to the popup rectangle.

Phase 1 (fast): boot in 5M chunks, poll fb popup-rect (x200-600,y230-280)
  until dirty; save checkpoint at window start.
Phase 2 (hooked): restore, run the window with an fb-write hook capturing
  (pc, pr, r4-r7) at the first rect writes.
"""
import sys
sys.path.insert(0, ".")
from emu import fw as F
from emu.machine import Machine
import numpy as np

PX0, PX1, PY0, PY1 = 200, 600, 230, 280
W = 800
FB0 = 0x0E780000


def rect_nz(mem):
    a = np.frombuffer(bytes(mem.fb[0]), dtype="<u2").reshape(480, 800)
    return int((a[PY0:PY1, PX0:PX1] != 0).sum())


def main():
    flash, _ = F.load_flash()
    m = Machine(flash)
    c, mem = m.cpu, m.mem
    n = 0
    m.save("/tmp/popup_prev.pkl")
    while True:
        c.run(limit=5_000_000)
        n += 1
        nz = rect_nz(mem)
        if n % 4 == 0 or nz:
            print(f"{n * 5}M pc={c.pc:#x} popup_px={nz}", flush=True)
        if nz > 100 or n * 5 >= 500:
            break
        m.save("/tmp/popup_prev.pkl")
    print("dirty; restoring window start and re-running hooked")
    m2 = Machine.load("/tmp/popup_prev.pkl")
    c2, mem2 = m2.cpu, m2.mem
    hits = []

    def hook(lo, hi):
        # any overlap with the popup rect?
        a0 = max(lo, FB0)
        a1 = min(hi, FB0 + 2 * 800 * 480)
        if a0 >= a1 or len(hits) >= 12:
            return
        o0, o1 = (a0 - FB0) // 2, (a1 - FB0) // 2
        xs = set()
        for o in range(o0, min(o1, o0 + 4000)):
            x, y = o % W, o // W
            if PX0 <= x < PX1 and PY0 <= y < PY1:
                xs.add((x, y))
        if xs:
            hits.append((hex(c2.pc), hex(c2.pr),
                         [hex(c2.gr(r)) for r in (4, 5, 6, 7)]))

    mem2.fb_hook = hook
    c2.run(limit=5_000_000)
    mem2.fb_hook = None
    print("rect px after re-run:", rect_nz(mem2))
    print("first popup-rect writers:")
    for h in hits:
        print("  pc=", h[0], "pr=", h[1], "r4-7=", h[2])


if __name__ == "__main__":
    main()
