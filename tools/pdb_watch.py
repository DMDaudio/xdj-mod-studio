"""pdb_watch.py -- does the emulated deck ever reach the code that uses the
"PIONEER/rekordbox/export.pdb" path strings?

Facts (static): /tmp/dec4.bin holds the full path twice (0x777be PGM-table
form + 0x7958a plain form; SH base 0x08000000). This tool arms Machine.watch
on all four string starts and runs boot + mount + Browse, printing PC, pr
and the r-register block at each first hit.
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
# string starts (file offsets in /tmp/dec4.bin) -> SH addresses
STRS = [0x777ac, 0x777d4, 0x79578, 0x79598]
ADDS = [0x08000000 + o for o in STRS]
STEPS = 900_000_000


def main():
    for o, a in zip(STRS, ADDS):
        with open("/tmp/dec4.bin", "rb") as f:
            f.seek(o)
            tag = f.read(32)
        print(f"watch {a:#x}: {tag!r}")
    flash, _ = F.load_flash(None)
    m = Machine(flash)
    dev = m.mem.dev
    disk = FolderDisk(FOLDER)
    dev.usb_disk = disk
    dev._usb().stick = MassStorage(disk)

    hits = []

    def cb(cpu):
        hits.append((cpu.pc, cpu.pr))
        print(f"HIT pc={cpu.pc:#x} pr={cpu.pr:#x}", flush=True)

    m.watch(ADDS, cb)
    m.cpu.run(limit=500_000_000)
    print("boot done, hits:", len(hits))
    for key in (["Browse"], ["DevUSB"]):
        dev.panel_frame = P.build_frame(key)
        m.cpu.run(limit=25_000_000)
        dev.panel_frame = P.build_frame()
        m.cpu.run(limit=5_000_000)
    print("after browse, hits:", len(hits))
    m.cpu.run(limit=STEPS - 560_000_000)
    print(f"final steps={m.cpu.steps:,} hits={len(hits)}")
    pdb = [k for k, p in disk.access if "export" in k.lower()]
    print("export.pdb sector reads:", len(pdb))


if __name__ == "__main__":
    main()
