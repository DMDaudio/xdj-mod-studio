#!/usr/bin/env python3
"""illegal_check.py [TAB] -- run the PERFORM pads in the emulator (py core),
tap a tab (default 3 = STEMS), collect every pc executed inside our cave and
check each instruction with capstone (the reference SH-4 decoder): flags
encodings capstone rejects (illegal on the real CPU) and illegal delay-slot
instructions (a branch/rts/trapa/... right after a delayed branch)."""
import struct
import sys
sys.path[:0] = [".", "tools"]
import capstone                                    # noqa: E402
from emu import panl                               # noqa: E402
from emu.machine import Machine                    # noqa: E402
import perform_pads as PP                          # noqa: E402

tab = int(sys.argv[1]) if len(sys.argv) > 1 else 3
m = Machine.load("cache/emu_perform_track.pkl", core="py")
PP.install(m.mem)
c, mem = m.cpu, m.mem
PCS = set()
LO, HI = PP.PC0, PP.PEND


def run(n, frame=None):
    mem.dev.panel_frame = frame or panl.build_frame()
    c.run(limit=int(n))


run(4e6)
c.trace = lambda pc, op: PCS.add(pc) if LO <= pc < HI else None
TAB = (PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[tab] + PP.TAB_H // 2)
run(3e6, panl.build_frame(touch=TAB))
run(8e6)
c.trace = None
md = capstone.Cs(capstone.CS_ARCH_SH, capstone.CS_MODE_SH4 | capstone.CS_MODE_LITTLE_ENDIAN)
DELAYED = {"bra", "bsr", "jmp", "jsr", "rts", "rte", "braf", "bsrf", "bt/s", "bf/s"}
NOSLOT = DELAYED | {"bt", "bf", "trapa", "ldc", "mova"}   # mova/pc-rel: check separately
bad = []
for pc in sorted(PCS):
    w = struct.pack("<H", mem.r16(pc) & 0xFFFF)
    ins = list(md.disasm(w, pc))
    if not ins:
        bad.append((hex(pc), f"capstone rejects {w[::-1].hex()}"))
        continue
    mn = ins[0].mnemonic
    if mn in DELAYED:
        w2 = struct.pack("<H", mem.r16(pc + 2) & 0xFFFF)
        i2 = list(md.disasm(w2, pc + 2))
        m2 = i2[0].mnemonic if i2 else "?"
        if m2 in DELAYED or m2 in ("bt", "bf", "trapa") or not i2:
            bad.append((hex(pc), f"{mn} with {m2} in its delay slot"))
        elif i2 and "pc" in i2[0].op_str and m2.startswith("mov"):
            bad.append((hex(pc), f"{mn} with PC-relative load in its slot ({m2} {i2[0].op_str})"))
print(f"tab {tab}: {len(PCS)} of our instructions executed")
for b in bad:
    print("  PROBLEM", b)
print("RESULT:", "clean" if not bad else f"{len(bad)} suspicious")
