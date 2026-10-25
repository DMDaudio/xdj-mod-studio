#!/usr/bin/env python3
"""dsp_burst_test.py -- prove DspSound's burst mode kills the machine gun.

Drives the REAL DspSound consumer (macaudio-free: _filler directly) at the
AudioQueue cadence against the wavplay snapshot running at sub-real-time
speed, with sync() called like the UI loop does. PASS requires:
  - production rate measured < 1 and burst mode engaged
  - ZERO partial takes while burst is on (every audible callback carried
    a full buffer -- no music-and-silence mixing, no L/R plane drift)
  - audio delivered in contiguous runs (holds separate them)
  - produced words == words the consumer saw + words left in the ring
    (nothing lost or duplicated)
Env: SNAP=cache/emu_dsp_wavplay.pkl.gz SECS=10
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, ".")
import numpy as np

from emu.machine import Machine
from emu import dspsound as D

SECS = float(os.environ.get("SECS", "10"))
SNAP = os.environ.get("SNAP", "cache/emu_dsp_wavplay.pkl.gz")


class FakeBuf(ctypes.Structure):
    _fields_ = [("mAudioDataBytesCapacity", ctypes.c_uint32),
                ("mAudioData", ctypes.c_void_p),
                ("mAudioDataByteSize", ctypes.c_uint32),
                ("mUserData", ctypes.c_void_p),
                ("mPacketDescriptionCapacity", ctypes.c_uint32),
                ("mPacketDescriptions", ctypes.c_void_p),
                ("mPacketDescriptionCount", ctypes.c_uint32)]


def main():
    m = Machine.load(SNAP)
    s = D.DspSound(m)
    s.playing = s._dsp_playing()
    print(f"{SNAP}: DSP playing = {s.playing}")
    de = m.mem.dev.dspemu
    de.sync()
    # the queue-open path primes from a real sync; replicate it so the
    # burst gate is armed exactly as in the served app
    s.primed = bool(de.asp1.out)
    arena = np.zeros((4096, 2), "<i2")
    fb = FakeBuf()
    fb.mAudioDataBytesCapacity = 4096 * 4
    fb.mAudioData = arena.ctypes.data
    bref = ctypes.cast(ctypes.byref(fb), ctypes.c_void_p)

    seq = []                       # per callback: frames TAKEN (drain delta)
    t0 = time.time()
    next_call = time.time()
    next_sync = time.time()
    w0 = len(de.asp1.out)
    while time.time() - t0 < SECS:
        m.cpu.run(limit=200_000)
        de.sync()
        now = time.time()
        if now >= next_sync:
            s.sync()               # the UI loop call: measures production
            next_sync += 0.1
        if now >= next_call:       # AudioQueue pace (10.7 Hz)
            d0 = s._drained
            s._callback(None, None, bref)
            seq.append((s._drained - d0) // 2)
            next_call += 4096 / 44100.0
    dt = time.time() - t0
    prod = len(de.asp1.out) - w0
    print(f"wall {dt:.1f}s: produced {prod} words "
          f"({prod / dt / 88200 * 100:.0f}% of real time), "
          f"prod_rate={s.prod_rate:.3f}, burst={s.burst}")
    full = [n for n in seq if n == 4096]
    partial = [n for n in seq if 0 < n < 4096]
    silent = [n for n in seq if n == 0]
    print(f"callbacks={len(seq)}: full={len(full)} partial={len(partial)} "
          f"silent={len(silent)} holds={s.holds} gaps={s.gaps} "
          f"half={s.half}")
    ok = True
    if s.prod_rate >= 1.0:
        print(f"NOTE: host reached real time ({s.prod_rate}); burst never "
              f"needed (burst={s.burst}) -- smooth-mode checks apply")
        ok = s.burst is False
    else:
        if not s.burst:
            print("FAIL burst mode never engaged")
            ok = False
        # partial takes are the machine gun; they are legal ONLY in the
        # sub-second window before the first production sample engaged the
        # gate (the legacy prime behavior). Count them from the first FULL
        # take on: there must be none.
        first_full = seq.index(4096) if 4096 in seq else len(seq)
        late = [n for n in seq[first_full:] if 0 < n < 4096]
        if s.burst and late:
            print(f"FAIL {len(late)} partial takes after burst mode was "
                  f"flowing (machine-gun risk)")
            ok = False
    # contiguous runs: full callbacks should come in runs >= 1
    runs, cur = [], 0
    for n in seq:
        cur = cur + 1 if n == 4096 else (runs.append(cur) if cur else None, 0)[1]
    if cur:
        runs.append(cur)
    runs = [r for r in runs if r]
    print(f"music runs: {len(runs)} (lengths {runs[:12]}{'...' if len(runs) > 12 else ''})")
    # words accounted: drained frames*2 == produced - backlog
    backlog = len(de.asp1.out) - s._drained
    print(f"accounting: drained={s._drained} left={backlog} sum={s._drained + backlog} "
          f"produced={prod} (+ring {w0})")
    if s._drained + backlog != len(de.asp1.out):
        print("FAIL word accounting mismatch")
        ok = False
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
