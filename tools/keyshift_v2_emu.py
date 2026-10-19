#!/usr/bin/env python3
"""keyshift_v2_emu.py [pitch|tempo] -- emulator proof for keyshift_v2_apply.

Boots cache/emu_track_loaded.pkl (HLE DSP), presses PLAY + MASTER TEMPO,
installs the same 2-byte NOP as the deck tool, writes +2 / -2 / 0 semis
into the slot's context word and checks the DSP mailbox echo: owned slot
== ours, other slot == unity, transport mode 5 and position advancing.
(Stub DSP: proves the SH side only; key-vs-tempo is the deck's call.)
"""
import os, sys
os.environ["XDJ_HLE_DSP"] = "1"
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl
from keyshift_v2_apply import VARIANTS, NOP, q20

slot = sys.argv[1] if len(sys.argv) > 1 else "pitch"
site, stock, target = VARIANTS[slot]
own, other = (0x11837BD8, 0x11837BC0) if slot == "pitch" else (0x11837BC0, 0x11837BD8)
m = Machine.load("cache/emu_track_loaded.pkl"); hle = m.mem.dev.hle
w = lambda a: int.from_bytes(bytes(m.mem.r8(a + i) for i in range(4)), "little")
def wr(a, v):
    for i, b in enumerate(v.to_bytes(4, "little")): m.mem.w8(a + i, b)
def run(t): m.cpu.run(limit=int(t * 500_000_000))
def frame(keys=()): m.mem.dev.panel_frame = panl.build_frame(keys=keys, fader_raw=250)
frame(); run(0.5)
frame(("PlayPause",)); run(0.15); frame(); run(0.8)
frame(("MasterTempo",)); run(0.15); frame(); run(0.8)
assert w(0x0B6C4264) == 5 and w(0x0B6C4308) == 1, "not playing with MT on"
assert m.mem.r8(site) | m.mem.r8(site + 1) << 8 == stock
m.mem.w8(site, NOP & 0xFF); m.mem.w8(site + 1, NOP >> 8)
good = True
for s in (2, -2, 0):
    V = q20(s); wr(target, V); ok = 0; p0 = hle.pos_samples
    for i in range(10):
        run(0.1)
        ok += hle.echo.get(own) == V and hle.echo.get(other) == 0x100000
    adv = hle.pos_samples - p0
    print(f"{s:+d} semis: {ok}/10  own={hle.echo.get(own, 0):#x} "
          f"other={hle.echo.get(other, 0):#x} mode={w(0x0B6C4264)} pos+{adv}")
    good &= ok == 10 and w(0x0B6C4264) == 5 and adv > 0
print("RESULT:", "PASS" if good else "FAIL")
