#!/usr/bin/env python3
"""boot_realusb.py [STICK] [OUT] -- fresh C-core boot with a real stick attached.

Boots MAIN from reset with the folder/image STICK plugged in from the
start, runs to idle (default 6B steps), screenshots, saves OUT.
Defaults: STICK=~/Downloads/Rekordbox stuff, OUT=cache/emu_realusb.pkl
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

STICK = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/Downloads/Rekordbox stuff")
OUT = sys.argv[2] if len(sys.argv) > 2 else "cache/emu_realusb.pkl"
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
    fb_png(m, "screenshots/realusb_boot.png")
    m.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    main()
