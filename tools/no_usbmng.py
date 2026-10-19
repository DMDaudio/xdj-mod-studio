#!/usr/bin/env python3
"""no_usbmng.py -- does the firmware open export.pdb if USBMNG.DAT is gone?

FolderDisk subclass: every served cluster of PIONEER/USBANLZ/USBMNG.DAT is
blanked to FAT zeros AFTER the parent's normal path (which also keeps the
access log intact, so the log still shows how much the firmware tried).
"""
import os
import sys
sys.path.insert(0, ".")
from collections import Counter
from emu.machine import Machine
from emu import fw as F
from emu import panl as P
from emu import usbdisk as U
from emu.usbhost import MassStorage

FOLDER = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
STEPS = 700_000_000
HIDE = os.path.join(FOLDER, "PIONEER/USBANLZ/USBMNG.DAT")


class HiddenUsbMngDisk(U.FolderDisk):
    """Blank every cluster served from USBMNG.DAT (log untouched)."""

    def _cluster(self, c, off):
        import bisect
        data = bytearray(super()._cluster(c, off))
        i = bisect.bisect_right(self._starts, c) - 1
        if i >= 0:
            c0, n, it = self.extents[i]
            if c < c0 + n and it["kind"] == "file" and it["path"] == HIDE:
                data = bytearray(b"\0" * len(data))
        return bytes(data)


def main():
    flash, _ = F.load_flash(None)
    m = Machine(flash)
    dev = m.mem.dev
    disk = HiddenUsbMngDisk(FOLDER)
    dev.usb_disk = disk
    dev._usb().stick = MassStorage(disk)
    m.cpu.run(limit=STEPS)
    counts = Counter()
    for kind, pos in disk.access:
        if kind.startswith("DIR:"):
            counts["DIR:" + os.path.relpath(kind[4:], FOLDER)] += 1
        else:
            counts[os.path.relpath(kind, FOLDER)] += 1
    for k, n in counts.most_common(20):
        print(f"{n:5d}  {k}")
    ex = [k for k in counts if "export" in k.lower()]
    print("export attempts:", ex if ex else "NONE")
    print("steps:", f"{m.cpu.steps:,}")


if __name__ == "__main__":
    main()
