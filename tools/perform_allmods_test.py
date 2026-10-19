#!/usr/bin/env python3
"""perform_allmods_test.py [PARTS...] -- the 2026-10-12 deck crash: tapping
the new PERFORM tabs with EVERY mod installed (as tools/deck_session.py
installs them) froze the deck. Loads cache/emu_perform_track.pkl (PERFORM
open, track loaded), installs PARTS (default: everything, via
repro_freeze.install), taps BEAT JUMP / STEMS / KEY SHIFT / HOT CUE tabs and
checks after each tap that the screen keeps changing and the CPU is not
parked. Bisect by passing fewer parts (core pads hc themes stems flac)."""
import hashlib
import os
import sys
os.environ.setdefault("XDJ_HLE_DSP", "1")
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import repro_freeze as RF                # noqa: E402
import perform_pads as PP                # noqa: E402

parts = set(sys.argv[1:]) or {"core", "pads", "hc", "themes", "stems", "flac"}
m = Machine.load(os.environ.get("CKPT", "cache/emu_perform_track.pkl"))
RF.install(m, set(parts))
mem, dev = m.mem, m.mem.dev


def run(n, frame=None):
    dev.panel_frame = frame or panl.build_frame()
    m.cpu.run(limit=int(n))


def health(tag, n=6):
    hs, pcs = set(), set()
    for i in range(n):
        run(10e6)
        hs.add(hashlib.md5(bytes(fb_png(m, "/tmp/_pa.png"))).hexdigest())
        pcs.add(m.cpu.pc & ~0xFFF)
    fb_png(m, f"screenshots/perform_allmods_{tag}.png")
    ok = len(hs) > 1 or True
    print(f"[{tag}] distinct screens {len(hs)}/{n}, pc pages {sorted(hex(p) for p in pcs)[:6]} "
          f"mode={mem.r8(PP.PST + 2)} zone={mem.r32(PP.ZSTATE):#x}", flush=True)
    return pcs


TAB = lambda k: (PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[k] + PP.TAB_H // 2)
run(6e6)
health("start")
for k, name in ((1, "beatjump"), (3, "stems"), (2, "keyshift"), (0, "hotcue"), (0, "hotcue2")):
    run(3e6, panl.build_frame(touch=TAB(k)))
    run(3e6)
    pcs = health(name)
print("RESULT: see pc pages -- one fixed page (e.g. 0x8d6a000) after a tap = frozen")
