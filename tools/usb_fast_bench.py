#!/usr/bin/env python3
"""usb_fast_bench.py -- deterministic track-load benchmark for XDJ_USB_FAST.

Starts from cache/emu_realusb.pkl, swaps to /tmp/faststick, mounts, browses
and loads sine.wav with fixed step counts (identical both modes). Reports:
  fb hash, SCSI ops/errors, file bytes served, wall time, MB/min, fast stats.

Usage:
  .venv/bin/python tools/usb_fast_bench.py [--steps N]  (default load wait 200M)
  XDJ_USB_FAST=1 .venv/bin/python tools/usb_fast_bench.py
"""
import sys, os, time, hashlib
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu.usbdisk import open_disk
from emu.usbhost import MassStorage
from emu import panl

LOAD_WAIT = int(os.environ.get("BENCH_WAIT", "200000000"))

def fbhash(m):
    reg = m.mem.dev.reg
    base = sum(reg.get(0xFE940430 + i, 0) << (8 * i) for i in range(4))
    idx = 1 if (base & 0x1FFFFFFF) == 0x0E83B800 else 0
    return hashlib.sha256(bytes(m.mem.fb[idx])).hexdigest(), idx

def main():
    from emu import usb_fast as UF
    UF.reset_stats()
    t_setup0 = time.time()
    m = Machine.load("cache/emu_realusb.pkl")
    dev, mem = m.mem.dev, m.mem
    dev.panel_frame = panl.build_frame()
    u = dev._usb()
    # instrument SCSI (popup_hunt pattern: zero bad statuses expected)
    ops = []
    errs = []
    # swap to faststick (deterministic small stick, read-only host files)
    u.unplug()
    m.cpu.run(limit=20_000_000)
    disk = open_disk("/tmp/faststick")
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    u.plug()
    for _ in range(3):
        m.cpu.run(limit=100_000_000)
    # re-wire instrumentation to the NEW stick (unplug swap replaced it)
    _os = u.stick._status
    _sc = u.stick._scsi
    def status(st, residue=0, *a, **k):
        if st != 0:
            errs.append((st, tuple(u.stick.sense), m.cpu.steps))
        return _os(st, residue, *a, **k)
    def scsi(cb, dlen, flags, *a, **k):
        ops.append((hex(cb[0]), dlen, m.cpu.steps))
        return _sc(cb, dlen, flags, *a, **k)
    # need closure over current stick methods (rebind after each swap? only once)
    u.stick._status = status
    u.stick._scsi = scsi
    t_setup = time.time() - t_setup0

    def key(k, hold=3_000_000, after=15_000_000):
        dev.panel_frame = panl.build_frame(keys=[k])
        m.cpu.run(limit=hold)
        dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=after)

    def turn(n, after=12_000_000):
        mem.w16(panl.ROTARY_COUNTER, (mem.r16(panl.ROTARY_COUNTER) + n) & 0xFFFF)
        m.cpu.run(limit=after)

    key("Browse")
    turn(1)
    for _ in range(3):
        key("RotaryPush", after=30_000_000)
    # load phase timing starts at the push that loads the track
    file_bytes_before = sum(1 for a, _ in disk.access if isinstance(a, str) and "sine.wav" in a)
    t0 = time.time()
    steps0 = m.cpu.steps
    dev.panel_frame = panl.build_frame(keys=["RotaryPush"])
    m.cpu.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=LOAD_WAIT)
    dt = time.time() - t0
    steps1 = m.cpu.steps
    # count sine.wav bytes served during load phase (sectors * 512)
    sine_secs = sum(1 for a, _ in disk.access if isinstance(a, str) and "sine.wav" in a)
    sine_bytes = sine_secs * 512
    # total file bytes (all host files) served in load phase approx: use access growth?
    # For MB/min use sine bytes (the track) over load wall time.
    mbmin = (sine_bytes / 1048576) / (dt / 60) if dt > 0 else 0
    h, idx = fbhash(m)
    from collections import Counter
    hist = Counter(o[0] for o in ops)
    print(f"mode={'FAST' if os.environ.get('XDJ_USB_FAST')=='1' else 'SLOW'}")
    print(f"setup {t_setup:.1f}s steps {steps0} -> {steps1} (+{steps1-steps0})")
    print(f"load wall {dt:.2f}s for {LOAD_WAIT/1e6:.0f}M steps = {(steps1-steps0)/dt/1e6:.1f}M steps/s")
    print(f"sine sectors {sine_secs} bytes {sine_bytes} ({sine_bytes/1048576:.2f} MB)")
    print(f"MB/min {mbmin:.1f}")
    print(f"scsi ops {len(ops)} errs {len(errs)} hist {dict(hist)}")
    print(f"fb {h[:16]} idx {idx}")
    print(f"faststats {UF.stats}")
    if errs:
        print(f"BAD STATUSES: {errs[:10]}")
    else:
        print("SCSI zero bad statuses")
    return 0

if __name__ == "__main__":
    sys.exit(main())
