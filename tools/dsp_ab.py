#!/usr/bin/env python3
"""dsp_ab.py SNAP STEPS [CHECKS] -- hash the real-DSP machine state at
CHECKS evenly spaced points while running STEPS SH steps: every DSP RAM
region, DSP registers + control registers, DSP cycle, the DAC word stream,
and the SH-side state (tools/differ.py snapshot_state). Run it twice with
an env toggle and compare the printed lines, e.g.

    XDJ_DSP_DFF=0 .venv/bin/python tools/dsp_ab.py cache/emu_dsp_wavplay.pkl.gz 3e8 6
    XDJ_DSP_DFF=1 .venv/bin/python tools/dsp_ab.py cache/emu_dsp_wavplay.pkl.gz 3e8 6

Identical output = identical machines at every checkpoint. Checkpoints go
through cpu.settle(), so a fast-forwarded delay call is observed mid-way."""
import hashlib
import pickle
import sys
import time

sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from differ import snapshot_state        # noqa: E402


def dsp_hash(m):
    d = m.mem.dev.dspemu
    c = d.cpu
    if hasattr(c, "settle"):
        c.settle()
    h = hashlib.sha1()
    seen = set()
    for lo, hi, buf in d.bus.ram:
        if id(buf) not in seen:
            seen.add(id(buf))
            h.update(bytes(buf))
    h.update(repr([int(x) for x in c.r]).encode())
    h.update(repr(sorted((k, int(v)) for k, v in dict(c.c).items())).encode()
             if hasattr(c.c, "items") else repr(list(c.c)).encode())
    h.update(repr((c.cycle, c.pc, c.stall, c.ev, c.pend, bool(c.sp))).encode())
    h.update(repr(list(d.asp1.out)).encode())
    sh = hashlib.sha1(pickle.dumps(sorted(
        (k, v.tobytes() if hasattr(v, "tobytes") else v)
        for k, v in snapshot_state(m).items()
        if k != "d_cyc"))).hexdigest()[:12]   # d_cyc: wobbles run to run
                                               # (pre-existing, see differ)
    return h.hexdigest()[:12], sh, c.cycle, len(d.asp1.out)


def main():
    snap, steps = sys.argv[1], int(float(sys.argv[2]))
    checks = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    m = Machine.load(snap)
    t = time.time()
    run = 0
    for i in range(checks):
        k = steps // checks
        m.cpu.run(limit=k)
        run += k
        dh, sh, cyc, nout = dsp_hash(m)
        print(f"check {i}: dsp {dh} sh {sh} cycle {cyc} dac {nout}")
    print(f"wall {time.time() - t:.1f}s")
    c = m.mem.dev.dspemu.cpu
    if hasattr(c.lib, "c6x_dff_stat"):
        print(f"(dff hits {c.lib.c6x_dff_stat(c.h, 0)}, "
              f"{c.lib.c6x_dff_stat(c.h, 1) / 1e6:.1f}M cycles)", file=sys.stderr)


if __name__ == "__main__":
    main()
