#!/usr/bin/env python3
"""ks_boot_compare.py -- EMULATOR ONLY, offline.

A/B compare of the key-shift reload call context, watching the DSP-side
handshake LIVE (the event log does not survive pickling, so this runs the
boot in-process per mode and reports per-slice state):

  masked  mode: flag 'KSR1'          (v6 default: the emu passes)
  unmasked mode: flag 'KSR1'|0x100    (v6 bit: reproduces the deck's 0x101)

Per 10M-step slice it records: SH pc (inside which accessor), ksr_status,
DSP pc + cycles, HPIC, HINT pin state, FFF0/FFF4/FFEC/FFFC (the words the
pipe ring points at), and the first slice where FFF4 or FFF0 == 1 (the
accessor poll's success value) in each mode.

Usage: .venv/bin/python tools/ks_boot_compare.py [snapshot]
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from keyshift_reload_emu import syms, B  # noqa: E402

SNAP = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_mounted6.pkl"
POOL_FLUSH = 0x08C92E20

ACCESSORS = (0x08C374F2, 0x08C37554, 0x08C37634, 0x08C376AE, 0x08C3771A)


def which_asm(pc):
    if pc < 0x08C3777C and pc >= 0x08C374F2:
        if pc < 0x08C37554:
            return "st0-poll"
        if pc < 0x08C37634:
            return "st1"
        if pc < 0x08C376AE:
            return "st2"
        if pc < 0x08C3771A:
            return "st3/4"
        return "st5"
    if 0x08C3777C <= pc < 0x08C378C0:
        return "BOOT"
    if pc == 0x09800040:
        return "CAVE"
    return "other"


def run_mode(tag, flag, S, blob, base_cave, verbose=36):
    t0 = time.time()
    m = Machine.load(SNAP)
    mem, d = m.mem, m.mem.dev.dspemu
    m.cpu.run(limit=10_000_000)
    for i, b in enumerate(blob):
        mem.w8(base_cave + i, b)
    prev = mem.r32(POOL_FLUSH)
    mem.w32(S["ksr_prev"], prev)
    mem.w32(POOL_FLUSH, base_cave)
    mem.w32(S["ksr_flag"], flag)
    first1 = None
    n_seen = 0
    st = 0
    for i in range(200):
        m.cpu.run(limit=10_000_000)
        st = mem.r32(S["ksr_status"])
        dsp_pc = d.cpu.pc
        f0 = d.bus.read(0x1183FFF0, 4)
        f4 = d.bus.read(0x1183FFF4, 4)
        fe = d.bus.read(0x1183FFEC, 4)
        fc = d.bus.read(0x1183FFFC, 4)
        hint = 1 if d.hpic & 4 else 0
        if (f0 == 1 or f4 == 1) and first1 is None:
            first1 = (i, f0, f4, hex(dsp_pc), d.cycles / 1e6)
        line = (f"[{tag}] +{(i + 1) * 10}M st={st:#x} sh={which_asm(m.cpu.pc)}"
                f" dsp={dsp_pc:#x}/{d.cycles/1e6:.0f}M hpic={d.hpic:#x}"
                f" pin={d.busy_pin()} FFF0={f0:#x} FFF4={f4:#x}"
                f" FFEC={fe:#x} FFFC={fc:#x}")
        if st or i < verbose or i % 10 == 19:
            print(line, flush=True)
        if st:
            break
    print(f"[{tag}] DONE st={st:#x} firstFFF1={first1} "
          f"boot_ret_dbg={mem.r32(S['ksr_dbg'] + 40):#x} "
          f"[{time.time() - t0:.0f}s]", flush=True)
    # who wrote the pipeline words? dump the sh-side ring too
    ring = [mem.r32(0x0C8440 + 4 * k) for k in range(8)]
    print(f"[{tag}] ring post:", " ".join(f"{v:#x}" for v in ring), flush=True)
    return st


def main():
    S = syms()
    blob = open(os.path.join(B, "ksr.bin"), "rb").read()
    base = S["ksr_entry"]
    for tag, flag in (("masked", 0x4B535231), ("unmasked", 0x4B535331)):
        run_mode(tag, flag, S, blob, base)
    return 0


def other(v):
    return v


if __name__ == "__main__":
    sys.exit(main())
