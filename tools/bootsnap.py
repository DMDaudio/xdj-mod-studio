#!/usr/bin/env python3
"""bootsnap.py -- staged boot snapshots for the task-birth hunt.

Boots the firmware with the fast C core and records, every STAGE steps:
  pc, steps, sha1 of ram0 (change detection), count of code-pointers in
  the task-work RAM (0x0A8B0000-0x0A8C0000), PnlCom state words, USB host
  regs, panel RX counter, DMA0 ch5 (display) state.

Usage: .venv/bin/python tools/bootsnap.py [stages] [per-stage-steps]
  default: 12 stages x 350M steps  (~4.2B, ~= full boot to idle)
Output: /tmp/bootstages.pkl  (list of dicts)
"""
import hashlib
import pickle
import struct
import sys

STAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 12
PER = int(sys.argv[2]) if len(sys.argv) > 2 else 350_000_000

sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F

d = open("/tmp/dec4.bin", "rb").read()


def is_code(v):
    if 0x08080000 <= v <= 0x091B0000:
        o = v - 0x08000000
        return d[o:o + 2] == b"\x22\x4f"
    return False


flash, _ = F.load_flash(None)
m = Machine(flash)  # C core
dev = m.mem.dev
out = []
for s in range(STAGES):
    m.cpu.run(limit=PER)
    ram = bytes(m.mem.ram0)
    n = len(ram) // 4
    words = struct.unpack("<%dI" % n, ram)
    lo, hi = (0x0A8B0000 - 0x08000000) // 4, (0x0A8C0000 - 0x08000000) // 4
    codes = sum(1 for i in range(lo, hi) if is_code(words[i]))
    usb = dev._usb()
    snap = {
        "stage": s,
        "steps": m.cpu.steps,
        "pc": m.cpu.pc,
        "sha": hashlib.sha1(ram).hexdigest()[:12],
        "taskarea_codeptrs": codes,
        "pnl_state": [m.mem.r32(a) for a in
                      (0x0B0E46F8, 0x0B0E4700, 0x0B0E4B30, 0x0B0E4B2D)],
        "syscfg": usb.r.get(0, 0),
        "panel_rx": getattr(dev, "panel_rx_frames", 0),
        "dma0ch5": dict(dev.dma[0xFE008000]["ch"][5]) if
        getattr(dev, "dma", None) else None,
    }
    out.append(snap)
    print(f"stage {s}: steps={snap['steps']:,} pc={snap['pc']:#x} "
          f"sha={snap['sha']} taskptrs={codes} "
          f"pnl={snap['pnl_state'][0]:#x},{snap['pnl_state'][1]:#x} "
          f"syscfg={snap['syscfg']:#x} panelrx={snap['panel_rx']}",
          flush=True)
pickle.dump(out, open("/tmp/bootstages.pkl", "wb"))
print("saved /tmp/bootstages.pkl")
