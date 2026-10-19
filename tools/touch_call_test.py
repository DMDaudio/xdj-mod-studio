#!/usr/bin/env python3
"""touch_call_test.py -- manually invoke the touch validator trampoline.

The PnlCom tasks never run in emu, so nothing calls the validator chain
(trampoline 0x8D53A24 -> thunk 0x8DA9096 -> assembler 0x8C5E81A). This
test writes real touch bytes into PAY, then CALLS the trampoline by CPU
state dance, and
checks whether the assembled slots + snapshot + downstream UI wake.

Usage: .venv/bin/python tools/touch_call_test.py [r4_hex]
"""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu.sh4 import PcHalt

CKPT = os.path.expanduser("~/emu-ckpt/emu_clean4.pkl")
MAGIC = 0x08000118
TRAMP = 0x08D53A24
PAY = 0x0B6C7A30
SLOTS = 0x0B6C7A50
SNAP = 0x0B6C40B8


def call(m, target, r4=None, timeout=2_000_000):
    c = m.cpu
    if r4 is not None:
        c.set_gr(4, r4 & 0xFFFFFFFF)
    c.sr |= 0x10000000
    old_pr = c.pr
    c.pr = MAGIC
    c.pc = target

    def tr(pc, op):
        if pc == MAGIC:
            raise PcHalt(pc)
    c.trace = tr
    try:
        ret = c.run(limit=timeout)
    except PcHalt:
        ret = "returned"
    finally:
        c.trace = None
        c.pr = old_pr
    return ret


def dump(m, tag):
    mem = m.mem
    def rd(a, n):
        return " ".join(f"{mem.r8(a + i):02x}" for i in range(n))
    print(f"[{tag}]")
    print(f"  PAY+20..26 : {rd(PAY + 20, 7)}")
    print(f"  slots 50-5f : {rd(SLOTS, 16)}")
    print(f"  snapshot    : {rd(SNAP, 16)}")
    print(f"  fb_writes   : {mem.fb_writes}")


def main():
    r4 = int(sys.argv[1], 16) if len(sys.argv) > 1 else PAY
    m = Machine.load(CKPT, core="py")
    mem = m.mem
    dump(m, "before")

    # real pad-A touch bytes
    for off, v in ((21, 0x01), (22, 0x03), (23, 0x12), (24, 0x02), (25, 0x2B)):
        mem.w8(PAY + off, v)
    print(f"PAY poked with touch: down=1 Y=0x0312 P=0x022B")

    # count downstream PC hits during a post-call run
    WATCH = {0x08D4A7A2: "snapshot", 0x08D2FE5C: "touchdisp",
             0x08C5E81A: "assembler", 0x08D682B8: "Yreader"}
    hits = {a: 0 for a in WATCH}

    # 1) unpack payload -> staging 0x0BA34EB8 (0x8DA90A2, r4=source)
    ret = call(m, 0x08DA90A2, r4=r4)
    print(f"unpack call -> {ret!r} (r4={r4:08x})")
    st = " ".join(f"{mem.r8(0x0BA34EB8 + i):02x}" for i in range(32))
    print(f"  staging 0x0BA34EB8: {st}")

    # 2) accept: trampoline -> assembler copies staging into slot buffer
    ret = call(m, TRAMP)
    print(f"trampoline call -> {ret!r}")
    dump(m, "after unpack+accept")

    def tr(pc, op):
        if pc in hits:
            hits[pc] += 1
    m.cpu.trace = tr
    ret = m.cpu.run(limit=3_000_000)
    m.cpu.trace = None
    print(f"post-run {ret!r}: " +
          ", ".join(f"{n}={hits[a]}" for a, n in WATCH.items()))
    dump(m, "after 3M steps")


if __name__ == "__main__":
    main()
