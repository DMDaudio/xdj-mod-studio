#!/usr/bin/env python3
"""touch_emu_test.py -- does the firmware touch chain wake on REAL touch bytes?

Loads emu_clean4 (fresh lineage, UI up, panel RX flowing), runs a control
phase (default all-zero panel frame) and a touch phase (frame carrying the
live-captured 2026-10-02 pad-A touch: PAY+21=1, Y=0x0312, P=0x022B),
counting hits on the known touch-path PCs and dumping panel-struct state
after each phase.

Watched:
  0x08D51A64 PANL parser      0x08D4A7A2 snapshotter
  0x08D53A24 validator tramp  0x08D682B8 Y-slot reader
  0x08D2FE5C touchdisp
"""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import panl

CKPT = os.path.expanduser("~/emu-ckpt/emu_clean4.pkl")
WATCH = [0x08D51A64, 0x08D4A7A2, 0x08D53A24, 0x08D682B8, 0x08D2FE5C]
NAMES = {0x08D51A64: "parser", 0x08D4A7A2: "snapshot",
         0x08D53A24: "validator", 0x08D682B8: "Yreader", 0x08D2FE5C: "touchdisp"}


def frame_touch(down=1, y=0x0312, p=0x022B):
    p30 = bytearray(30)
    p30[21] = down
    p30[22] = (y >> 8) & 0xFF
    p30[23] = y & 0xFF
    p30[24] = (p >> 8) & 0xFF
    p30[25] = p & 0xFF
    fr = bytearray(p30)
    fr.append(panl.checksum(p30))
    fr.append(0x8F)
    return bytes(fr)


def rd(mem, addr, n):
    return " ".join(f"{mem.r8(addr + i):02x}" for i in range(n))


def phase(m, frame, steps, tag):
    m.mem.dev.panel_frame = frame
    hits = {a: 0 for a in WATCH}
    c = m.cpu

    def tr(pc, op):
        if pc in hits:
            hits[pc] += 1
    c.trace = tr
    ret = c.run(limit=steps)
    c.trace = None
    print(f"--- {tag} (run -> {ret!r}) ---")
    for a in WATCH:
        print(f"  {NAMES[a]:10s} @{a:08x}: {hits[a]} hits")
    mem = m.mem
    print(f"  PAY+20..26 : {rd(mem, 0x0B6C7A44, 7)}")
    print(f"  slots 50-5f: {rd(mem, 0x0B6C7A50, 16)}")
    print(f"  mode       : {rd(mem, 0x0B6C4264, 4)}")
    print(f"  panel_rx   : {getattr(m.mem.dev, 'panel_rx_frames', '?')} frames")
    return hits


FLAGS_DOWN = [(0x0B6C7A98, 0xF0), (0x0B6C7A9C, 0xFC),
               (0x0B6C7A64, 0xEC), (0x0B6C7A82, 0x80)]


def poke_flags(m):
    for addr, v in FLAGS_DOWN:
        m.mem.w8(addr, v)


def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 3_000_000
    m = Machine.load(CKPT, core="py")
    print(f"loaded {CKPT}, panel_rx at start: "
          f"{getattr(m.mem.dev, 'panel_rx_frames', '?')}")
    phase(m, panl.build_frame(), steps, "control (no touch)")
    phase(m, frame_touch(), steps, "touch (pad A: down=1 Y=0312 P=022b)")
    poke_flags(m)
    phase(m, frame_touch(), steps, "touch + DOWN flags (deck ghost-tap)")
    phase(m, frame_touch(down=0, y=0x0312, p=0x0001), steps,
          "release (down=0)")


if __name__ == "__main__":
    main()
