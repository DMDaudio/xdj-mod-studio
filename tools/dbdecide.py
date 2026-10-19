#!/usr/bin/env python3
"""dbdecide.py -- catch the library-DB decision at runtime.

PC-watches (Machine.watch, first-hit per address) on:
  * DJDB function cluster 0x080C2000-0x080C3400 step 4 (runtime finding:
    shoots repeatedly right after enumeration)
  * PGM/DJDB _MSG struct pools 0x08077880 / 0x08079500
  * export.pdb + "%c:/%s" format strings 0x080797D8/0x080797E8
Everything fires once and prints PC + pr + a non-zero register block.

SPEED: stage 1 boots to the post-enumeration mount state with NO watches
(C core, checkpoint adbdecide_pre.pkl); stage 2 loads it with core="py",
arms watches, runs on. Run with `stage1` to (re)build, no arg = stage 2.
"""
import os
import sys
sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F
from emu import panl as P
from emu.usbdisk import FolderDisk
from emu.usbhost import MassStorage

FOLDER = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
PGM_POOL = 0x08077880
DJDB_POOL = 0x08079500
FORMATTERS = [0x080797D8, 0x080797E8]
CLUSTER = [0x080C2000 + a for a in range(0x0, 0x1400, 4)]
PRE = "cache/dbdecide_pre.pkl"


def regdump(cpu):
    parts = []
    pr = getattr(cpu, "pr", 0)
    for i in range(16):
        v = getattr(cpu, f"r{i}", 0)
        if v:
            parts.append(f"r{i}={v:#x}")
    return f"pr={pr:#x} " + " ".join(parts)


def stage1():
    flash, _ = F.load_flash(None)
    m = Machine(flash)
    dev = m.mem.dev
    disk = FolderDisk(FOLDER)
    dev.usb_disk = disk
    dev._usb().stick = MassStorage(disk)
    m.cpu.run(limit=500_000_000)          # enumeration + FAT walk + browse
    for key in (["Browse"], ["DevUSB"]):
        dev.panel_frame = P.build_frame(key)
        m.cpu.run(limit=25_000_000)
        dev.panel_frame = P.build_frame()
        m.cpu.run(limit=5_000_000)
    m.save(PRE)
    print("stage1 saved", m.cpu.steps)


def stage2():
    m = Machine.load(PRE, core="py")
    dev = m.mem.dev
    addrs = sorted(set(CLUSTER) | {PGM_POOL, DJDB_POOL, *FORMATTERS})
    m.watch(addrs, lambda cpu: print(
        f"HIT pc={cpu.pc:#x} {regdump(cpu)}", flush=True))
    print(f"armed {len(addrs)} addresses; running from checkpoint...",
          flush=True)
    m.cpu.run(limit=250_000_000)
    print("=== stage2 done; steps total:", f"{m.cpu.steps:,}", "===")


if __name__ == "__main__":
    stage1() if "stage1" in sys.argv[1:] else stage2()
