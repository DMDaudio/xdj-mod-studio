#!/usr/bin/env python3
"""ks_diag_failtest.py -- EMULATOR ONLY, offline.

Validates the DIAG ksr build's failure path in the emulator without touching
the deck. Setup is exactly keyshift_reload_emu.py's install (cave bytes,
ksr_prev, pool -> cave, flag 'KSR1'), but DspDevice.read answers 0xFFFFFFFF
for every HPID read AFTER the cave's first probe (pre-probe stays honest;
the boot sees a dead host port = a plausible signature of the deck's 0x101).

Expected:
  status 0x101 (boot routine failed all 3 tries),
  ksr_dbg = [real, savedSR, 0xFFFFFFFF, 0xFFFFFFFF, 1, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF]
(the post-fail probes recorded the dead-HPI signature).

Usage: .venv/bin/python tools/ks_diag_failtest.py [snapshot] [out]
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu.c6x import dspdev               # noqa: E402
from keyshift_reload_emu import syms, B  # noqa: E402

SNAP = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_mounted6.pkl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "cache/emu_ks_diag_fail.pkl"
POOL_FLUSH = 0x08C92E20


def main():
    t0 = time.time()
    S = syms()
    blob = open(os.path.join(B, "ksr.bin"), "rb").read()
    m = Machine.load(SNAP)
    mem = m.mem
    d = mem.dev.dspemu
    assert d is not None and d.running, "snapshot needs the real DSP running"

    base = S["ksr_entry"]
    m.cpu.run(limit=10_000_000)
    for i, b in enumerate(blob):
        mem.w8(base + i, b)
    prev = mem.r32(POOL_FLUSH)
    mem.w32(S["ksr_prev"], prev)
    mem.w32(POOL_FLUSH, base)

    # -- injection: HPID reads go dead from the 2nd host-port read onward
    orig_read = d.read
    state = {"n": 0, "first": None}

    def dead_read(a, size):
        if 0x18080000 <= a < 0x180C0004:      # HPID window only
            state["n"] += 1
            if state["first"] is None:
                state["first"] = a
            if state["n"] > 1:
                return (1 << (8 * size)) - 1  # floating-bus read
        rv = orig_read(a, size)
        return rv
    d.read = dead_read

    mem.w32(S["ksr_flag"], 0x4B535231)
    print(f"installed at {base:#x}, inject from HPID read #2", flush=True)
    st = 0
    for i in range(200):
        m.cpu.run(limit=10_000_000)
        st = mem.r32(S["ksr_status"])
        if st:
            break
    dbg = [mem.r32(S["ksr_dbg"] + 4 * k) for k in range(22)]
    names = ["HPIC pre", "HPIA pre", "ring0", "ring1", "ring2", "ring3",
             "ring4", "ring5", "ring6", "ring7", "BOOT ret",
             "HPIC post", "HPIA post", "ring0p", "ring1p", "ring2p",
             "ring3p", "ring4p", "ring5p", "ring6p", "ring7p", "pins"]
    print(f"status {st:#x} after ~{(i + 1) * 10}M steps [{time.time() - t0:.0f}s]",
          flush=True)
    for n, v in zip(names, dbg):
        print(f"  {n:10s} {v:#010x}", flush=True)
    # v2 expectations: the boot must fail (r=1), the cave must NOT hang
    # (status written, pool restored), the pipe ring must not advance
    # (never serviced) and the last HPI target must be in the poll region.
    hpia_post = dbg[12]
    ok = (st == 0x101 and dbg[10] == 1
          and mem.r32(POOL_FLUSH) == prev
          and dbg[0] == dbg[11] and dbg[2:10] == dbg[13:21]   # ring untouched
          and 0x1183FFE0 <= dbg[12] <= 0x1183FFF8)
    m.save(OUT)
    print("saved", OUT, "RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
