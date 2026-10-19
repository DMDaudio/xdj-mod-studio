#!/usr/bin/env python3
"""exportwatch_pool.py -- who reads the PGM/DJDB struct and export.pdb strings?

Runtime, from cache/dbdecide_pre.pkl (post-mount checkpoint, py core):
  * PC-watches on the actual PC/size of the 4 export.pdb string refs in the
    recording RECORD TABLE:
       fp->string-slot + leading pool / record table / other runs:
         0x080BD191A hits code region 0x0BD10..-0x0BD2BC ; confirmed at 0xBD1822
         0x080BA71AC hits code region 0x0BA7134..-0x0BA71C8 pairs (code block)
    (the strings themselves are DATA; runtime PC trace of *loads* of these
     pool words is impossible without a full memory watch; watch only code)
  * DATA-ADDR watch mode: use m.mem.watch + dev watch on the string ADDRESS
    range to record who reads bytes there (mem.watch is a read/write
    watch on the given RAM address, single-hit per run)

Also PC-watches the clean code/function 0x080BD1822 that owns the
record table (modeled pattern: jsr @rN with a move-string into a table).

Usage: .venv/bin/python tools/exportwatch_pool.py [secs]
"""
import sys
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import panl as P

CKPT = "cache/dbdecide_pre.pkl"
PC_SITES = [
    (0x080BD1822, "BD1822-fn-block-start"),
    (0x080BD1826, "BD1826-after-pool-before-branch"),
    (0x080BD182E, "BD182E-branch-target"),
    (0x080BA71C0, "BA71C0-inner-fn"),
    (0x080BA71AC, "BA71AC-pool-site"),
    (0x080BA71B8, "BA71B8-pool-site2"),
]


def regdump(cpu):
    parts = [f"r{i}={v:#x}" for i in range(16)
             if (v := getattr(cpu, f"r{i}", 0))]
    pr = getattr(cpu, "pr", 0)
    return f"pr={pr:#x} " + " ".join(parts)


def main():
    m = Machine.load(CKPT, core="py")
    addrs = [a for a, _ in PC_SITES]
    m.watch(addrs, lambda cpu: print(
        f"HIT pc={cpu.pc:#x} {regdump(cpu)}", flush=True))
    labels = dict(PC_SITES)
    dev = m.mem.dev
    # run browse then soak
    for key in (["Browse"], ["DevUSB"]):
        dev.panel_frame = P.build_frame(key)
        m.cpu.run(limit=25_000_000)
        dev.panel_frame = P.build_frame()
        m.cpu.run(limit=5_000_000)
    m.cpu.run(limit=200_000_000)
    print("done; steps total:", f"{m.cpu.steps:,}")


if __name__ == "__main__":
    main()
