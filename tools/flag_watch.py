#!/usr/bin/env python3
"""flag_watch.py [STEPS] -- who reads/writes the touch flag+slot area in emu?

Monkey-patches mem.r8phys/w8phys to log access to 0x0B6C7A40..0x0B6C7AA0
(panel struct tail: assembled slots + DOWN-flag bytes), sets fresh DOWN
values first, runs, and prints (pc, addr, old->new) events.

Usage: .venv/bin/python tools/flag_watch.py 3000000
"""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine

CKPT = os.path.expanduser("~/emu-ckpt/emu_clean4.pkl")
LO, HI = 0x0B6C7A40, 0x0B6C7AA4
FLAGS = {0x0B6C7A64: 0xEC, 0x0B6C7A82: 0x80,
         0x0B6C7A98: 0xF0, 0x0B6C7A9C: 0xFC}


def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 3_000_000
    m = Machine.load(CKPT, core="py")
    mem = m.mem
    events = []
    orig_r, orig_w = mem.r8phys, mem.w8phys

    def r8(a):
        if LO <= a < HI:
            events.append(("R", m.cpu.pc, a, None))
        return orig_r(a)

    def w8(a, v):
        if LO <= a < HI:
            events.append(("W", m.cpu.pc, a, v & 0xFF))
        return orig_w(a, v)

    mem.r8phys = r8
    mem.w8phys = w8
    for a, v in FLAGS.items():
        mem.w8(a, v)
    print(f"flags set: " +
          ", ".join(f"{a:08x}={v:02x}" for a, v in FLAGS.items()))
    ret = m.cpu.run(limit=steps)
    mem.r8phys = orig_r
    mem.w8phys = orig_w
    print(f"run -> {ret!r}; {len(events)} events in struct tail")
    # summarize by pc
    from collections import Counter
    by_pc = Counter((ev[0], ev[1]) for ev in events)
    for (kind, pc), n in by_pc.most_common(25):
        print(f"  {kind} from pc={pc:08x}: {n}x")
    # show first few W events with values
    ws = [e for e in events if e[0] == "W"][:12]
    for _, pc, a, v in ws:
        print(f"  W {a:08x} <- {v:02x} (pc {pc:08x})")


if __name__ == "__main__":
    main()
