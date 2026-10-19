#!/usr/bin/env python3
"""popup_hunt2.py -- why does browse-open raise "Media or File is
collapsed or unavailable" on the user's stick? Logs every SCSI status
!= 0 (+sense) and the disk accesses around it, on the exact app path:
clean stickless snapshot -> insert his stick -> DevUSB -> settle.
"""
import os
import sys
import time

sys.path[:0] = [".", "tools"]
os.environ.setdefault("XDJ_USB_FAST", "1")
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
from emu.usbdisk import open_disk        # noqa: E402
from emu.usbhost import MassStorage      # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402

STICK = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
SNAP = os.environ.get(
    "SNAP", os.path.expanduser("~/Library/Application Support/"
                               "XDJ Mod Studio/firmware/dspsound.pkl.gz"))

m = Machine.load(SNAP)
mem, dev = m.mem, m.mem.dev
d = dev.dspemu
d.ratio = 0.1
errs, ops = [], []


def run(n):
    m.cpu.run(limit=int(n))


u = dev._usb()
disk = open_disk(STICK)
dev.usb_disk = disk
u.stick = MassStorage(disk)
u.stick._status0 = u.stick._status
u.stick._scsi0 = u.stick._scsi

def status(st, residue=0, *a, **k):
    if st != 0:
        errs.append(("STATUS", st, tuple(u.stick.sense), m.cpu.steps,
                     len(disk.access)))
    return u.stick._status0(st, residue, *a, **k)

def scsi(cb, dlen, flags, *a, **k):
    ops.append((hex(cb[0]), dlen, m.cpu.steps))
    return u.stick._scsi0(cb, dlen, flags, *a, **k)
u.stick._status = status
u.stick._scsi = scsi

print("inserting; waiting for mount (FAT walk)", flush=True)
t0 = time.time()
for _ in range(12):
    run(250e6)
    if u.stick.addr:
        break
if not u.stick.addr:
    u.unplug(); run(100e6)
    for _ in range(8):
        run(200e6)
print(f"addr {u.stick.addr} [{time.time()-t0:.0f}s], settling", flush=True)
run(500e6)


def key(k, n=15e6):
    dev.panel_frame = panl.build_frame(keys=[k])
    run(3e6)
    dev.panel_frame = panl.build_frame()
    run(n)


if os.environ.get("SAVE"):                 # snapshot right before DevUSB
    m.save(os.environ["SAVE"])
    print("saved", os.environ["SAVE"], flush=True)
key("DevUSB", 120e6)
fb_png(m, "screenshots/popup_usb.png")
ops_before = len(ops)
run(300e6)
fb_png(m, "screenshots/popup_settled.png")
print(f"after DevUSB+300M: scsi ops {len(ops)}, errors {len(errs)}", flush=True)
for e in errs[-10:]:
    print("  ERR", e, flush=True)
# last 30 distinct accesses
last = disk.access[-40:]
print("last accesses:", flush=True)
for p, o in last:
    print(f"   {str(p)[-60:]} @ {o}", flush=True)
# op mix
from collections import Counter
print("op mix:", Counter(o[0] for o in ops).most_common(8), flush=True)
if os.environ.get("DIRS"):
    seen = []
    for p, o in disk.access:
        k = str(p)
        if k.startswith("DIR:") or "PIONEER" in k:
            k = k.replace(STICK, "")
            if k not in seen:
                seen.append(k)
    print("ALL PIONEER/DIR touches in order:", flush=True)
    for k in seen:
        print("   ", k, flush=True)
