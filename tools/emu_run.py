#!/usr/bin/env python3
"""Marathon driver: run the emulated boot in checkpointed chunks.

    .venv/bin/python tools/emu_run.py --ckpt FILE --steps N [--load]

--load resumes from FILE, otherwise boots fresh from the .UPD. Runs N
CPU steps in --chunk pieces with an atomic save after each, so a kill
never loses more than one chunk and never corrupts the checkpoint.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from emu.machine import Machine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="/tmp/emu_boot.pkl")
    ap.add_argument("--steps", type=int, default=50000000)
    ap.add_argument("--chunk", type=int, default=20000000)
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--upd", default=None)
    ap.add_argument("--stop-on-fault", action="store_true",
                    help="halt at the first new fault (keeps pre-storm state)")
    a = ap.parse_args()

    if a.load and os.path.exists(a.ckpt):
        m = Machine.load(a.ckpt, flash_path=a.upd)
        print(f"resumed {a.ckpt}: pc={m.cpu.pc:#010x} steps={m.cpu.steps:,}", flush=True)
    else:
        from emu import fw as F
        flash, info = F.load_flash(a.upd)
        print(f"loaded {info.get('source')} ({len(flash):,} B)", flush=True)
        m = Machine(flash)
    done = 0
    while done < a.steps:
        n = min(a.chunk, a.steps - done)
        res, dt = m.run(limit=n, stop_on_fault=a.stop_on_fault)
        done += n
        ram_mb = (sum(1 for b in m.mem.ram0 if b) +
                  sum(1 for b in m.mem.ram1 if b)) / 1048576.0
        print(f"[{done // 1000000}M] {res} steps={m.cpu.steps:,} "
              f"excs={m.exc_total} ram={ram_mb:.1f}MB "
              f"fb_writes={m.mem.fb_writes} "
              f"({m.cpu.steps / max(dt, 1e-9):,.0f} steps/s)", flush=True)
        if m.excs_first:
            spc, off, code, tea, sr, sp, pr = m.excs_first[0]
            print(f"  first fault: spc={spc:#010x} vec+{off:#x} code={code:#x} "
                  f"tea={tea:#010x} sr={sr:#010x} sp={sp:#010x}",
                  flush=True)
        m.save(a.ckpt, flash_path=a.upd)
        print(f"  saved {a.ckpt}", flush=True)
        if a.stop_on_fault and m.exc_total:
            break


if __name__ == "__main__":
    main()
