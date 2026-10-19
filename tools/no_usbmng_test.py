"""no_usbmng_test.py -- decisive test: does USBMNG.DAT's presence divert the
XDJ-700 firmware away from export.pdb (folder view) toward the legacy DB?

Method: serve the user's real stick via a subclassed FolderDisk that hides
PIONEER/USBANLZ/USBMNG.DAT from enumeration (nothing else changes), boot,
press Browse, and diff the access log vs the control (exportwatch.py):
  control reads USBMNG.DAT fully, never opens rekordbox/.
  If the variant opens PIONEER/rekordbox/export.pdb -> cause proven, fix =
  hide/upgrade USBMNG.DAT (the 700's own library format is export.pdb;
  rekordbox 2026 re-export has BOTH).
"""
import os
import sys
from collections import Counter

sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F
from emu import panl as P
from emu.usbdisk import FolderDisk
from emu.usbhost import MassStorage

FOLDER = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
HIDE = os.path.join(FOLDER, "PIONEER", "USBANLZ", "USBMNG.DAT")
STEPS = 860_000_000


class NoUsbMngDisk(FolderDisk):
    """FolderDisk minus one file: hide USBMNG.DAT at scan time, BEFORE the
    FAT/extents are built (filtering afterwards leaves the file's clusters
    mapped and served -- the first run proved that)."""

    def __init__(self, root, hide):
        self._hide = os.path.abspath(hide)
        super().__init__(root)

    def _scan(self, path, parent):
        if os.path.abspath(path) == self._hide:
            return None                       # file hidden entirely
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
    disk = NoUsbMngDisk(FOLDER, HIDE)
    dev.usb_disk = disk
    dev._usb().stick = MassStorage(disk)
    m.cpu.run(limit=500_000_000)
    for key in (["Browse"], ["DevUSB"]):
        dev.panel_frame = P.build_frame(key)
        m.cpu.run(limit=25_000_000)
        dev.panel_frame = P.build_frame()
        m.cpu.run(limit=5_000_000)
    m.cpu.run(limit=STEPS - 560_000_000)

    files = Counter()
    for kind, pos in disk.access:
        if kind.startswith("DIR:"):
            files["DIR " + kind[4:]] += 1
        elif kind != "SECTOR":
            files[kind] += 1
    pdb = [k for k in files if "export" in k.lower() or "rekordbox" in k.lower()]
    print("=== variant (USBMNG.DAT hidden) ===")
    print("steps", m.cpu.steps)
    for k, n in files.most_common(20):
        print(f"  {n:5d}x {k}")
    print("PDB/rekordbox reads:", pdb if pdb else "NONE")


if __name__ == "__main__":
    main()
