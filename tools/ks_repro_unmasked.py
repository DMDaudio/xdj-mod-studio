#!/usr/bin/env python3
"""ks_repro_unmasked.py -- EMULATOR ONLY, offline.

Runs the v6 cave in UNMASKED mode (flag 'KSR1'|0x100 = 0x4B535331), which
reproduces the deck's 0x101 offline (2026-10-17: status after ~950M steps).
Then reads the DSP handshake words 0x1183FFF0/FFF4 both at the moment the
boot gave up and after settling, so the failing wait can be compared with
the passing (masked) runs.

Output: OUT snapshot + console listing of dbg + DSP handshake state.

Usage: .venv/bin/python tools/ks_repro_unmasked.py [snapshot out]
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from keyshift_reload_emu import syms, B  # noqa: E402

SNAP = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_mounted6.pkl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "cache/emu_ks_unmasked_fail.pkl"
POOL_FLUSH = 0x08C92E20


def main():
    t0 = time.time()
    S = syms()
    blob = open(os.path.join(B, "ksr.bin"), "rb").read()
    m = Machine.load(SNAP)
    mem, d = m.mem, m.mem.dev.dspemu
    m.cpu.run(limit=10_000_000)
    base = S["ksr_entry"]
    for i, b in enumerate(blob):
        mem.w8(base + i, b)
    prev = mem.r32(POOL_FLUSH)
    mem.w32(S["ksr_prev"], prev)
    mem.w32(POOL_FLUSH, base)
    mem.w32(S["ksr_flag"], 0x4B535331)          # 'KSR1' | unmasked
    print(f"installed at {base:#x}, flag KSR1|0x100 (unmasked)", flush=True)

    # a DSP-side write watcher on the handshake words
    cw = []                                     # (cycle, addr, value)
    orig_store = d._store
    store_log = d.log
    base_hpi = start = None

    st = 0
    for i in range(200):
        f4 = d.bus.read(0x1183FFF4, 4)
        f0 = d.bus.read(0x1183FFF0, 4)
        m.cpu.run(limit=10_000_000)
        st = mem.r32(S["ksr_status"])
        if st:
            f4 = d.bus.read(0x1183FFF4, 4)
            f0 = d.bus.read(0x1183FFF0, 4)
            break
        if (i + 1) % 20 == 0:
            print(f"  +{(i + 1) * 10}M: status {st:#x} FFF0 {f0:#x} "
                  f"FFF4 {f4:#x} dspcycles {d.cycles/1e6:.0f}M "
                  f"running={d.running} err={d.error}", flush=True)
    dbg = [mem.r32(S["ksr_dbg"] + 4 * k) for k in range(24)]
    names = ["HPIC pre", "HPIA pre", "ring0", "ring1", "ring2", "ring3",
             "ring4", "ring5", "ring6", "ring7", "BOOT ret",
             "HPIC post", "HPIA post", "ring0p", "ring1p", "ring2p",
             "ring3p", "ring4p", "ring5p", "ring6p", "ring7p", "pins",
             "SR pre-mask", "SR post-mask"]
    print(f"status {st:#x} after ~{(i + 1) * 10}M steps [{time.time() - t0:.0f}s]",
          flush=True)
    for n, v in zip(names, dbg):
        if v:
            print(f"  dbg {n:12s} {v:#010x}", flush=True)
    for k in range(3):
        m.cpu.run(limit=100_000_000)
        print(f"  +settle {(k + 1) * 100}M: FFF0 {d.bus.read(0x1183FFF0, 4):#x} "
              f"FFF4 {d.bus.read(0x1183FFF4, 4):#x} dspcycles "
              f"{d.cycles/1e6:.0f}M running={d.running} err={d.error} "
              f"pc={d.cpu.pc:#x}", flush=True)
    m.save(OUT)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    main()
