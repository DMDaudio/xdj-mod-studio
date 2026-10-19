#!/usr/bin/env python3
"""demostick_boot.py -- fresh boot with demo-stick attached, then Browse.

Proves the minimal fast stick mounts and lists its track.
OUT: screenshots/demostick_browse.png + cache/emu_demostick.pkl
"""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import fw as F
from emu import panl
from emu.usbdisk import open_disk
from emu.usbhost import MassStorage
from ghosttap_emu import fb_png

STICK = "demo-stick"
TOTAL = int(os.environ.get("STEPS", "6000000000"))


def main():
    flash, _ = F.load_flash(None)
    m = Machine(flash)
    dev = m.mem.dev
    dev.panel_frame = panl.build_frame()
    disk = open_disk(STICK)
    dev.usb_disk = disk
    dev._usb().stick = MassStorage(disk)
    while m.cpu.steps < TOTAL:
        m.cpu.run(limit=min(250_000_000, TOTAL - m.cpu.steps))
        print(f"{m.cpu.steps / 1e9:.2f}B  msg={m.mem.r32(0x0BA155A0):#x}",
              flush=True)
    # press Browse (panel key bit) and let the browser draw
    dev.panel_frame = panl.build_frame(keys=("Browse",))
    m.cpu.run(limit=500_000_000)
    dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=1_000_000_000)
    fb_png(m, "screenshots/demostick_browse.png")
    m.save("cache/emu_demostick.pkl")
    print("saved cache/emu_demostick.pkl")


if __name__ == "__main__":
    main()
