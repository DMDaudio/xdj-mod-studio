#!/usr/bin/env python3
"""ks_stock_reload_emu.py -- EMULATOR ONLY, offline.

Rehearses the key-shift cave's STOCK mode ('KSR0' flag, ksr.c v3): the
program copy stays unpatched (no key-shift append, no call re-point) and
the deck's boot routine reloads the audio chip from it.

Success = the routine returns 0 (status 1), the call word in the DSP is
STOCK (0x0027D290), the key-shift area at DSP 0xC005F4C0 is untouched,
the borrowed pools are restored, and the DSP keeps running with no error.

Usage: .venv/bin/python tools/ks_stock_reload_emu.py [snapshot] [out]
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from keyshift_reload_emu import syms, B  # noqa: E402

SNAP = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_mounted6.pkl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "cache/emu_ks_stock_ok.pkl"
POOL_FLUSH = 0x08C92E20
STOCK_CALL = 0x0027D290


def main():
    t0 = time.time()
    S = syms()
    blob = open(os.path.join(B, "ksr.bin"), "rb").read()
    ks = open(os.path.join(B, "ks.bin"), "rb").read()
    m = Machine.load(SNAP)
    mem, d = m.mem, m.mem.dev.dspemu
    m.cpu.run(limit=10_000_000)
    base = S["ksr_entry"]
    for i, b in enumerate(blob):
        mem.w8(base + i, b)
    prev = mem.r32(POOL_FLUSH)
    mem.w32(S["ksr_prev"], prev)
    mem.w32(POOL_FLUSH, base)
    mem.w32(S["ksr_flag"], 0x4B535230)          # 'KSR0' = STOCK mode
    print(f"installed at {base:#x}, flag KSR0 (stock copy)", flush=True)
    st = 0
    for i in range(200):
        m.cpu.run(limit=10_000_000)
        st = mem.r32(S["ksr_status"])
        if st:
            break
    call = d.bus.read(0xC0008D64, 4)
    ddr = bytes(d.bus.read(0xC005F4C0 + i, 1) for i in range(len(ks)))
    print(f"status {st:#x} after ~{(i + 1) * 10}M steps [{time.time() - t0:.0f}s] "
          f"flag {mem.r32(S['ksr_flag']):#x} "
          f"len {mem.r32(0x08D78AC8):#x} src {mem.r32(0x08C37918):#x} "
          f"pool {mem.r32(POOL_FLUSH):#x}", flush=True)
    for k in range(3):
        m.cpu.run(limit=100_000_000)
        print(f"  +{(k + 1) * 100}M: DSP err={d.error} running={d.running} "
              f"pc={d.cpu.pc:#x}", flush=True)
    ok = (st == 1 and call == STOCK_CALL and ddr != ks
          and d.error is None and d.running
          and mem.r32(POOL_FLUSH) == prev and mem.r32(0x08D78AC8) == 0x5F4C0
          and mem.r32(0x08C37918) == 0xA800E770)
    m.save(OUT)
    print("saved", OUT, "RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
