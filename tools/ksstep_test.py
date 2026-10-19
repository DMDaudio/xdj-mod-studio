#!/usr/bin/env python3
"""ksstep_test.py -- the KEY SHIFT tab steppers (tools/perform_pads.py
KSSTEP unit + pakey), called directly on fake version tables: semitone
stepping (>, <, RESET), index stepping (+, -) with wraps, miss-keeps
(no version carries the target shift -> keep current), corrupt-n safety,
and the keycode-high-half trap (the 2026-10-15 VSEL=145 bug: the u32 at
VTBL+8 holds n in the low half and the keycode up high -- readers must
mask, never dereference). ~30 s, no track load."""
import sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
import perform_pads as PP                # noqa: E402
import vredirect as VR                   # noqa: E402
from shasm import Asm                    # noqa: E402

m = Machine.load("cache/emu_perform_track.pkl", core="py")
PP.install(m.mem)
mem, c = m.mem, m.cpu
HALT = 0x0F300000
a = Asm()
a.assemble(PP.HOOK_SRC)
PAKEY = PP.PC0 + a.labels["pakey"]


def setup(n, semis, keycode=9):
    mem.w32(VR.VTBL, 0x1000)
    mem.w32(VR.VTBL + 4, 0x100000)
    mem.w16(VR.VTBL + 8, n)          # low half = count ...
    mem.w16(VR.VTBL + 10, keycode)  # ... high half = keycode (the trap)
    for i in range(8):
        mem.w8(PP.VSEMI + i, semis[i] if i < len(semis) else 0)


def call(entry, r0):
    c.set_gr(0, r0)
    c.pr = HALT
    c.pc = entry
    m.run(limit=1000000, stop_on_pc=[HALT])
    return mem.r8(VR.VSEL)


ok = True


def chk(tag, got, want):
    global ok
    good = got == want
    ok &= good
    print(("PASS " if good else "FAIL "), tag, "->", got,
          "" if good else f"(want {want})")


e0 = m.exc_total
# contiguous +1/+2 chain from mix
setup(2, [1, 2])
mem.w8(VR.VSEL, 0); chk("mix > -> +1", call(PP.KSSTEP_ENTRY, 62), 1)
mem.w8(VR.VSEL, 1); chk("+1 > -> +2", call(PP.KSSTEP_ENTRY, 62), 2)
mem.w8(VR.VSEL, 2); chk("+2 > keeps (no +3)", call(PP.KSSTEP_ENTRY, 62), 2)
mem.w8(VR.VSEL, 2); chk("+2 < -> +1", call(PP.KSSTEP_ENTRY, 60), 1)
mem.w8(VR.VSEL, 1); chk("+1 < -> mix", call(PP.KSSTEP_ENTRY, 60), 0)
mem.w8(VR.VSEL, 2); chk("RESET -> mix", call(PP.KSSTEP_ENTRY, 61), 0)
# gap file: mix has no +1 neighbour
setup(3, [0x7F, 0x7F, 2])
mem.w8(VR.VSEL, 0); chk("gap: mix > keeps", call(PP.KSSTEP_ENTRY, 62), 0)
mem.w8(VR.VSEL, 0); chk("gap: mix < keeps", call(PP.KSSTEP_ENTRY, 60), 0)
mem.w8(VR.VSEL, 3); chk("gap: key+2 < keeps", call(PP.KSSTEP_ENTRY, 60), 3)
# corrupt count: nothing may move or crash
setup(300, [1, 2])
mem.w8(VR.VSEL, 0); chk("corrupt n: > keeps", call(PP.KSSTEP_ENTRY, 62), 0)
# index stepper with wraps (semis irrelevant)
setup(3, [0x7F, 0x7F, 2])
mem.w8(VR.VSEL, 0); chk("+ 0->1", call(PAKEY, 21), 1)
mem.w8(VR.VSEL, 2); chk("+ 2->3", call(PAKEY, 21), 3)
mem.w8(VR.VSEL, 3); chk("+ 3->wrap 0", call(PAKEY, 21), 0)
mem.w8(VR.VSEL, 0); chk("- 0->wrap 3", call(PAKEY, 20), 3)
mem.w8(VR.VSEL, 2); chk("- 2->1", call(PAKEY, 20), 1)
print("exceptions:", m.exc_total - e0)
ok &= m.exc_total == e0
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
