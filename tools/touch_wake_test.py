#!/usr/bin/env python3
"""touch_wake_test.py [X Y] -- does a REAL touch (deck-decoded bytes) reach the
firmware's touch dispatch in the emu? Default tap = PERFORM button
(X=0x090, Y=0x06A; live capture 2026-10-05). PAY+21 down, +22/23 X BE,
+24/25 Y BE. Phases: idle / held / released; counts hits on the Rcv loop's
frame processor + dispatch and diffs the framebuffer.
"""
import sys
import time
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

CK = "cache/emu_mainscreen.pkl"
W = {0x08D507A0: "frameproc", 0x08DD8020: "dispatch", 0x08D682B8: "Yreader",
     0x08D2FE5C: "touchdisp", 0x08D53A24: "validator", 0x08DFFECC: "wup_tsk"}


def touch_frame(x, y, down=1):
    fr = bytearray(panl.build_frame())
    # byte 21 is NOT sent by the panel: the parser ORs bit0 into PAY+21 itself
    # once coords are valid (after 3 debounce frames). Forcing it here makes
    # the hit-tester latch "no zone" on the (0,0) debounce frames.
    if not down:          # deck release: X and Y drop to ~1 (<32 = no touch)
        x, y = 0x0001, 0x0001
    fr[22], fr[23] = x >> 8, x & 0xFF
    fr[24], fr[25] = y >> 8, y & 0xFF
    fr[30] = panl.checksum(fr[:30])
    return bytes(fr)


def phase(m, frame, steps, tag):
    m.mem.dev.panel_frame = frame
    h = {a: 0 for a in W}

    def tr(pc, op):
        if pc in h:
            h[pc] += 1
    m.cpu.trace = tr
    t = time.time()
    r = m.cpu.run(limit=steps)
    m.cpu.trace = None
    print(f"[{tag}] {time.time()-t:.0f}s {r!r}")
    print("   " + " ".join(f"{W[a]}={h[a]}" for a in W),
          f"gate={m.mem.r32(0x0A8BE2FC):#x}",
          f"PAY+21..25={bytes(m.mem.r8(0x0B6C7A30+21+i) for i in range(5)).hex()}")


def main():
    x, y = (int(a, 16) for a in sys.argv[1:3]) if len(sys.argv) > 2 else (0x090, 0x06A)
    m = Machine.load(CK, core="py")
    fb0 = fb_png(m, "screenshots/touchwake_0.png")
    phase(m, panl.build_frame(), 3_000_000, "idle")
    phase(m, touch_frame(x, y), 6_000_000, "down")
    fb1 = fb_png(m, "screenshots/touchwake_1.png")
    phase(m, touch_frame(x, y, 0), 16_000_000, "up")
    fb2 = fb_png(m, "screenshots/touchwake_2.png")
    d = lambda a, b: sum(1 for p, q in zip(a, b) if p != q)
    print(f"fbdiff down={d(fb0, fb1)} after={d(fb0, fb2)}")


if __name__ == "__main__":
    main()
