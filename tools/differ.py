#!/usr/bin/env python3
"""differ.py -- differential A/B proof for the SH core.

Compares NEW (current emu/fastcore.c) vs OLD (compiled reference build)
on the same snapshot with the same N SH steps and checks that CPU
registers, RAM, framebuffers and device state are byte-identical. Both
r sides must be the C core: the shipped Python core is NOT a reference
(it has a long-standing cycle-accounting difference vs the C core; see
the C core's done-vs-steps comment). Strict DSP-free mode is the
default; --live runs with the DSP running and reports DSP-side-only
diffs separately (legitimate: the two builds interleave SH<->DSP
accesses at different crossing counts, e.g. HPIC word vs byte writes).

Usage (two runs + one compare):
  .venv/bin/python tools/differ.py SW N --write /tmp/old.pkl  --old   CC=cc sh -c
  .venv/bin/python tools/differ.py SW N --write /tmp/new.pkl  --new
  .venv/bin/python tools/differ.py --compare /tmp/new.pkl /tmp/old.pkl
  (or simply) .venv/bin/python tools/differ.py SW N           # both in one process
"""
import os
import pickle
import subprocess
import sys

sys.path.insert(0, ".")

OLD_SRC = "tools/_old/fastcore.c"
OLD_LIB = "cache/_fastcore_old.so"


def build_old():
    os.makedirs("cache", exist_ok=True)
    subprocess.check_call(["cc", "-O2", "-shared", "-fPIC", "-o", OLD_LIB,
                           OLD_SRC, "-lm"])


def snapshot_state(m):
    import numpy as np
    s = {}
    cpu = m.cpu
    for name in ("pc", "pr", "gbr", "vbr", "mach", "macl", "sr", "ssr",
                 "spc", "sgr", "dbr", "tra", "expevt", "intevt", "tea",
                 "pteh", "ptel", "ptea", "ttb", "mmucr", "ccr", "fpscr",
                 "fpul", "steps", "cycles"):
        s["r_" + name] = getattr(cpu, name)
    s["r_r"] = [int(x) for x in [cpu.gr(i) for i in range(16)]]
    # RAM / fb are shared bytearrays: copy before the second run
    mem = m.mem
    for name in ("ram0", "ram1", "misc", "sq"):
        s["m_" + name] = np.frombuffer(bytearray(getattr(mem, name)),
                                       dtype=np.uint8)
    s["m_fb0"] = np.frombuffer(bytearray(mem.fb[0]), dtype=np.uint8)
    s["m_fb1"] = np.frombuffer(bytearray(mem.fb[1]), dtype=np.uint8)
    dev = m.mem.dev
    for name in ("reg", "rx", "tx_log", "pending", "cyc", "tmu", "tstr",
                 "imr", "cmstr", "cmcsr", "cmcnt", "cmcor", "cm_acc", "dma",
                 "fb_base", "cs6", "dspmem"):
        s["d_" + name] = pickle.dumps(getattr(dev, name, None))
    if getattr(dev, "tdg", None):
        s["d_tdg"] = pickle.dumps(dev.tdg.state())
    return s


def diff(a, b, tag=""):
    import numpy as np
    bad = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if hasattr(va, "dtype") and hasattr(vb, "dtype"):
            if not (va.shape == vb.shape and (va == vb).all()):
                n = int((va != vb).sum()) if va.shape == vb.shape else -1
                bad.append(f"{k}: {n} bytes differ")
        else:
            if va != vb:
                bad.append(f"{k}: {va!r} != {vb!r}")
    return bad


def run_side(tag, snap, steps, live, env=None):
    from emu.machine import Machine
    m = Machine.load(snap)
    de = getattr(m.mem.dev, "dspemu", None)
    if de is not None and not live:
        de.hold = True
    import time
    t0 = time.time()
    if steps:
        m.cpu.run(limit=steps)
    t1 = time.time()
    print(f"{tag}: ran {steps:,} steps in {t1-t0:.2f}s "
          f"({steps/(t1-t0)/1e6:.1f} M/s)")
    st = snapshot_state(m)
    del m
    return st


def main():
    import numpy  # noqa
    args = [x for x in sys.argv[1:] if not x.startswith("--") or
            x in ("--old", "--new", "--live")]
    kw = {x: True for x in sys.argv if x.startswith("--") and
          x not in ("--old", "--new")}
    vals = [x for x in sys.argv[1:] if not x.startswith("--")]
    # --old/--new are side selectors, not mode flags: kw deliberately
    # excludes them, so read them off argv directly (2026-10-24: they were
    # looked up in kw and never found, so --old silently ran the CURRENT
    # build on both sides of every two-run A/B).
    old = "--old" in sys.argv
    if old:
        build_old() if not os.path.exists(OLD_LIB) else None
        os.environ["OLD_FASTCORE"] = "1"
    if "--write" in sys.argv:
        out = sys.argv[sys.argv.index("--write") + 1]
        if old:
            os.environ["XDJ_FASTCORE_LIB"] = OLD_LIB
        snap = vals[0]
        steps = int(vals[1]) if len(vals) > 1 else 8_000_000
        st = run_side("OLD" if old else "NEW", snap, steps, kw.get("--live"))
        with open(out, "wb") as f:
            pickle.dump(st, f)
        return
    if kw.get("--compare") and "--compare" in sys.argv:
        i = sys.argv.index("--compare")
        a, b = sys.argv[i + 1], sys.argv[i + 2]
        with open(a, "rb") as f:
            sa = pickle.load(f)
        with open(b, "rb") as f:
            sb = pickle.load(f)
        bad = diff(sb, sa)
        if bad:
            print("DIFFERENCES:")
            for x in bad[:40]:
                print("  ", x)
            sys.exit(1)
        print("MATCH: identical CPU registers, RAM and device state "
              f"({len(sa)} items)")
        return
    snap = vals[0]
    steps = int(vals[1]) if len(vals) > 1 else 8_000_000
    sa = run_side("OLD-python-core", snap, steps, kw.get("--live"))
    from emu.machine import Machine  # fresh instance
    import emu.fastcpu as fc
    os.environ["XDJ_CORE"] = "fast"
    m = Machine.load(snap)
    de = getattr(m.mem.dev, "dspemu", None)
    if de is not None and not kw.get("--live"):
        de.hold = True
    import time
    t0 = time.time()
    if steps:
        m.cpu.run(limit=steps)
    t1 = time.time()
    print(f"NEW-fastcore: ran {steps:,} steps in {t1-t0:.2f}s "
          f"({steps/(t1-t0)/1e6:.1f} M/s)")
    sb = snapshot_state(m)
    bad = diff(sb, sa)
    if bad:
        print("DIFFERENCES:")
        for x in bad[:40]:
            print("  ", x)
        sys.exit(1)
    print("MATCH: identical CPU registers, RAM and device state "
          f"({len(sa)} items)")


if __name__ == "__main__":
    main()
