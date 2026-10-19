#!/usr/bin/env python3
"""exportwatch.py -- serve a folder as the USB stick, boot, open Browse,
and report EVERYTHING the firmware reads (file paths + offsets, plus
raw sector counts). The X-ray for a real rekordbox export: which DB it
opens, whether it reads ANLZ (and which parts), what a track load pulls.

Usage: .venv/bin/python tools/exportwatch.py <folder> [steps_M]
  default 600M steps (~6s C-core).
"""
import sys
from collections import Counter

sys.path.insert(0, ".")
from emu.machine import Machine
from emu import fw as F
from emu import panl as P
from emu.usbdisk import FolderDisk
from emu.usbhost import MassStorage

folder = sys.argv[1]
STEPS = int(sys.argv[2]) * 1_000_000 if len(sys.argv) > 2 else 600_000_000

flash, _ = F.load_flash(None)
m = Machine(flash)
dev = m.mem.dev
disk = FolderDisk(folder)
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
ranges = {}
sectors = 0
for kind, pos in disk.access:
    if kind == "SECTOR":
        sectors += 1
    elif kind.startswith("DIR:"):
        files["DIR " + kind[4:]] += 1
    else:
        files[kind] += 1
        r = ranges.setdefault(kind, [pos, pos])
        r[0] = min(r[0], pos)
        r[1] = max(r[1], pos)
print(f"steps={m.cpu.steps:,} raw-sectors={sectors} entries={len(disk.access)}")
print("--- file/dir reads (count, span) ---")
for k, n in files.most_common(25):
    span = ""
    if k in ranges:
        a, b = ranges[k]
        span = f" [{a:#x}-{b+0x200:#x}]"
    print(f"  {n:5d}x {k}{span}")
u = dev._usb()
print("attached:", u.attached, "syscfg:", hex(u.r.get(0, 0)))
print("scsi:", Counter(e[1] for e in u.stick.log if e[0] == "scsi"))
print("exc:", [e for e in u.stick.log
               if e[0] in ("disk-exc", "status-FAIL")][:3] or "none")
tail = disk.access[-12:]
print("--- last 12 accesses ---")
for k, p in tail:
    print(f"  {k} +{p:#x}" if isinstance(p, int) else f"  {k} {p}")
raw = [p for k, p in disk.access[-2000:] if k == "SECTOR"]
if raw:
    print(f"recent raw LBA range: {min(raw):#x}..{max(raw):#x} "
          f"({len(set(raw))} unique)")
