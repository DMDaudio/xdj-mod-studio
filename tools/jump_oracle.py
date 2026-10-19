#!/usr/bin/env python3
"""jump_oracle.py CKPT SEL -- run the 700's jump handler (0x8D4318E) in emulation.

Sets the handler's guards (track present, play-ish mode), calls it with a
selector 1..8, and logs every subroutine call (target + r4-r7) plus every
read from low/flash regions (table hunt). Stops at return-to-MAGIC,
fault, or step limit.
"""
import sys
sys.path.insert(0, ".")
from emu.machine import Machine
from emu.sh4 import PcHalt, SHException

MAGIC = 0x08000118
HANDLER = 0x08D4318E


def main():
    m = Machine.load(sys.argv[1], core="py")
    c, mem = m.cpu, m.mem
    sel = int(sys.argv[2])
    snap_ram0 = bytes(mem.ram0)
    snap_ram1 = bytes(mem.ram1)
    regs = (list(c.b0), list(c.b1), c.sr, c.pc, c.pr, c.gbr)

    def restore():
        mem.ram0[:] = snap_ram0
        mem.ram1[:] = snap_ram1
        c.b0[:], c.b1[:] = regs[0], regs[1]
        c.sr, c.pc, c.pr, c.gbr = regs[2], regs[3], regs[4], regs[5]

    restore()
    mem.w8(0x0B310424, 0x00)   # track present (guard != 0xFF)
    mem.w8(0x0B6C4264, 0x04)   # play-ish mode (guard in {3,4,5,7,8,9})
    c.sr |= 0x10000000
    c.b0[4] = sel
    c.pr = MAGIC
    c.pc = HANDLER
    calls, reads, faults = [], [], []

    orig_r8, orig_r32 = mem.r8, mem.r32

    def r8(a):
        v = orig_r8(a)
        if len(reads) < 60 and (a < 0x1000000 or 0x08000000 <= a < 0x0C000000):
            reads.append((hex(a), hex(v)))
        return v

    def r32(a):
        v = orig_r32(a)
        if len(reads) < 60 and (a < 0x1000000 or 0x08000000 <= a < 0x0C000000):
            reads.append((hex(a), hex(v)))
        return v

    mem.r8, mem.r32 = r8, r32

    def tr(pc, op):
        if pc == MAGIC:
            raise PcHalt(pc)
        # jsr @Rn = 0x4n0B, bsr = 0xBxxx: log target + args
        if (op & 0xF0FF) == 0x400B or (op >> 12) == 0xB:
            if len(calls) < 40:
                calls.append((hex(pc),
                              [hex(c.b0[r]) for r in (4, 5, 6, 7)]))

    c.trace = tr
    try:
        res = c.run(limit=200000)
        print("finished:", res)
    except PcHalt:
        print("returned to MAGIC (handler completed)")
    except SHException as e:
        print(f"fault: pc={c.pc:#x} off={e.offset:#x} code={e.code:#x}")
    except Exception as e:
        print(f"stop: {type(e).__name__} {e} at pc={c.pc:#x}")
    finally:
        c.trace = None
        mem.r8, mem.r32 = orig_r8, orig_r32
    print(f"--- sel={sel}: {len(calls)} calls ---")
    for pc, args in calls[:25]:
        print(f"  {pc} r4..r7={args}")
    print(f"--- {len(reads)} watched reads (first 25) ---")
    for a, v in reads[:25]:
        print(f"  [{a}] = {v}")


if __name__ == "__main__":
    main()
