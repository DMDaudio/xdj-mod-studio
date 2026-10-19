#!/usr/bin/env python3
"""bootrace.py -- headless fresh boot with USB-range access logging.

Boots the 700 firmware from reset (C core if available), logging every
FIRST touch (read/write+pc) to the USB controller window, and sampling
the framebuffer for first pixels. Reveals the boot-time USB init
sequence and whether the popup decision needs any stick at all.
Usage: bootrace.py [STEPS_M]  (default 60M steps, chunked with progress)
"""
import os
import sys
import time
sys.path.insert(0, ".")
from emu import fw as F
from emu.machine import Machine

USB0 = (0x04D80000, 0x04D80200)


def main():
    flash, info = F.load_flash()
    print("flash:", info.get("source"), len(flash))
    m = Machine(flash)
    c, mem = m.cpu, m.mem
    dev = mem.dev
    seen = {}
    orig_read, orig_write = dev.read, dev.write

    def read(a, s):
        if USB0[0] <= (a & 0x1FFFFFFF) < USB0[1] and a not in seen:
            seen[a] = ("R", hex(c.pc))
        return orig_read(a, s)

    def write(a, s, v):
        if USB0[0] <= (a & 0x1FFFFFFF) < USB0[1] and (a, 1) not in seen:
            seen[(a, 1)] = ("W", hex(c.pc), hex(v))
        return orig_write(a, s, v)

    dev.read, dev.write = read, write
    total = int(sys.argv[1]) * 1_000_000 if len(sys.argv) > 1 else 60_000_000
    t0 = time.time()
    done = 0
    chunk = 10_000_000
    import numpy as np
    while done < total:
        c.run(limit=chunk)
        done += chunk
        el = time.time() - t0
        fb = bytes(mem.fb[0][:64])
        print(f"{done // 1000000}M steps, {el:.0f}s, pc={c.pc:#x}, "
              f"usb-touched={len(seen)}", flush=True)
    print("--- USB first-touches ---")
    for a in sorted(seen, key=lambda x: (x[0] if isinstance(x, tuple) else x)):
        print(hex(a) if isinstance(a, int) else a, seen[a])
    print("done")


if __name__ == "__main__":
    main()
