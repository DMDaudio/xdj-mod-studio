#!/usr/bin/env python3
"""load_perf_probe.py -- where does the wall time go when the user inserts
the 13 GB stick and loads a track on the snapshot-dsp boot?

Stages (それぞれ with the exact panel keys the shipped app flow uses):
  1. restore snapshot
  2. insert stick + mount burst
  3. Browse press
  4. rotary to the track + RotaryPush (load)
  5. PLAY
Prints per-stage wall seconds and asp.out growth so we can see what to
speed up. Read-only on the stick.
"""
import os
import sys
import time

sys.path.insert(0, ".")
from emu.machine import Machine
from emu import panl as P
from emu.usbdisk import FolderDisk
from emu.usbhost import MassStorage

FOLDER = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
STEPS_PER = 400_000          # an app-like slice


def stage(m, dev, label, keys=(), seconds=8.0, fader=250, rotary=0):
    t0 = time.time()
    n0 = len(getattr(dev.dspemu.asp1, "out", [])) if dev.dspemu else 0
    while time.time() - t0 < seconds:
        for _ in range(27):     # ~10 Hz of frames like the UI loop
            dev.panel_frame = (P.build_frame(keys=keys, fader_raw=fader,
                                             rotary=rotary)
                               if keys else P.build_frame())
            m.cpu.run(limit=STEPS_PER)
        dev.panel_frame = P.build_frame()
        m.cpu.run(limit=STEPS_PER * 3)
    grew = 0
    if dev.dspemu:
        grew = len(dev.dspemu.asp1.out) - n0
    print(f"{label:24s} {time.time()-t0:6.1f}s  out+{grew}", flush=True)


def main():
    t0 = time.time()
    m = Machine.load("cache/emu_dsp_sound.pkl.gz")
    print(f"{'restore snapshot':24s} {time.time()-t0:6.1f}s", flush=True)
    dev = m.mem.dev
    t0 = time.time()
    disk = FolderDisk(FOLDER)
    dev._usb().unplug()
    m.cpu.run(limit=10_000_000)
    dev.usb_disk = disk
    usb = dev._usb()
    usb.stick = MassStorage(disk)
    # mount burst like insert_usb
    burst = m.cpu.steps + 2_400_000_000
    while m.cpu.steps < burst and time.time() - t0 < 60:
        m.cpu.run(limit=400_000)
        if len(disk.access) > 21000 and disk.access[-1][0] == "SECTOR" \
                and m.cpu.steps > burst - 200_000_000:
            pass
    # settle: run until access goes quiet
    quiet = 0
    while quiet < 40 and time.time() - t0 < 90:
        n = len(disk.access)
        m.cpu.run(limit=400_000)
        if len(disk.access) == n:
            quiet += 1
        else:
            quiet = 0
    print(f"{'USB mount':24s} {time.time()-t0:6.1f}s  ops={len(disk.access)}",
          flush=True)

    stage(m, dev, "Browse press", keys=["Browse"], seconds=4)
    stage(m, dev, "rot +3", seconds=3, rotary=3)
    stage(m, dev, "RotaryPush x3", keys=["RotaryPush"], seconds=4)
    stage(m, dev, "rot +2", seconds=3, rotary=2)
    stage(m, dev, "RotaryPush (load)", keys=["RotaryPush"], seconds=8)
    print("mode:", hex(m.mem.r32(0x0B6C4264)), flush=True)
    stage(m, dev, "PLAY", keys=["RotaryPush"], seconds=8)
    print("mode:", hex(m.mem.r32(0x0B6C4264)),
          "out:", len(dev.dspemu.asp1.out) if dev.dspemu else "-", flush=True)


if __name__ == "__main__":
    main()
