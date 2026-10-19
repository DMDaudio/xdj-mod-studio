#!/usr/bin/env python3
"""open_trace.py -- log the FIRST access to every stick file the firmware
touches, ordered. Answers: in this emulated boot, what file paths does the
XDJ-700 firmware try in what order, and does it ever attempt export.pdb?
Run twice: control (as-is) and variant (USBMNG.DAT hidden) to compare.

Output: per-file first-access table + the 600-byte window around the first
attempt at any rekordbox/ path.
"""
import os
import sys
sys.path.insert(0, ".")
from collections import Counter
from emu.machine import Machine
from emu import fw as F
from emu import panl as P
from emu.usbdisk import FolderDisk
from emu.usbhost import MassStorage

FOLDER = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
HIDE = os.path.join(FOLDER, "PIONEER/USBANLZ/USBMNG.DAT") if \
    sys.argv[1:] == ["nousbmng"] else None
STEPS = 900_000_000


class NoUsbMngDisk(FolderDisk):
    def __init__(self, root, hide):
        self._hide = os.path.abspath(hide)
        super().__init__(root)

    def _scan(self, path, parent):
        if os.path.abspath(path) == self._hide:
            return None
        it = {"kind": "dir", "path": path, "name": os.path.basename(path),
              "children": [], "parent": parent}
        self.items.append(it)
        try:
            names = sorted(os.listdir(path))
        except OSError:
            names = []
        for nm in names:
            if nm.startswith(".") or nm == "System Volume Information":
                continue
            p = os.path.join(path, nm)
            if os.path.abspath(p) == self._hide:
                continue
            if os.path.isdir(p):
                it["children"].append(self._scan(p, it))
            elif os.path.isfile(p):
                f = {"kind": "file", "path": p, "name": nm,
                     "size": os.path.getsize(p), "parent": it}
                self.items.append(f)
                it["children"].append(f)
        return it


def main():
    flash, _ = F.load_flash(None)
    m = Machine(flash)
    dev = m.mem.dev
    disk = NoUsbMngDisk(FOLDER, HIDE) if HIDE else FolderDisk(FOLDER)
    dev.usb_disk = disk
    dev._usb().stick = MassStorage(disk)
    m.cpu.run(limit=500_000_000)
    for key in (["Browse"], ["DevUSB"]):
        dev.panel_frame = P.build_frame(key)
        m.cpu.run(limit=25_000_000)
        dev.panel_frame = P.build_frame()
        m.cpu.run(limit=5_000_000)
    m.cpu.run(limit=STEPS - 560_000_000)

    order = []
    seen = set()
    for kind, pos in disk.access:
        if kind.startswith("DIR:"):
            k = kind[4:]
        else:
            k = kind
        if k not in seen:
            seen.add(k)
            order.append((len(order), k, pos))
    print("=== mode:", "variant (USBMNG.DAT hidden)" if HIDE else "control ===")
    print("files(dirs excluded) touched in order:")
    for i, k, pos in order:
        base = os.path.basename(k)
        if k.startswith("DIR") or not base:
            continue
        rel = os.path.relpath(k, FOLDER)
        print(f"  {i:4d}  +{pos:#010x}  {rel}")
    counts = Counter(os.path.relpath(k, FOLDER) for i, k, pos in order
                     if os.path.basename(k))
    rk = [k for k in counts if "export" in k.lower()]
    print("export.pdb attempts:", rk)


if __name__ == "__main__":
    main()
