#!/usr/bin/env python3
"""flac_full.py -- load the forged-ANLZ full-track FLAC in emu.

RETIRED 2026-10-11 (like tools/flac_emu_proof.py): the DeckVolve
black-box payload never engages in the emulator (no source to debug) and
the DeckVolve cave does NOT ship. Our own FLAC decoder layer is the
shipped path; its proofs are tools/xflac_test.py,
tools/xflac_vfile_test.py and tools/repro_freeze.py --only pads,stems,flac.
Kept for history only."""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu.usbdisk import open_disk
from emu.usbhost import MassStorage
from emu import panl
from ghosttap_emu import fb_png

ROOT = "/tmp/flacstick"
GATE_LO, GATE_HI = 0x080FB800, 0x080FC200
PAY_LO = 0x091BD7B0
PAY_HI = 0x091BD7B0 + 53228


def install_target(mem):
    import json
    import base64
    r = json.load(open("tools/data/flac_alpha1_template.json"))
    stock = open("cache/blk1.bin", "rb").read()
    t = bytearray(stock + b"\xff" * (r["target_decoded_size"] - len(stock)))
    for s in r["spans"]:
        dd = base64.b64decode(s["data"])
        t[s["offset"]:s["offset"] + len(dd)] = dd
    for op in r["owner_stock_copy_ops"]:
        src, dst, n = op["source_offset"], op["dest_offset"], op["size"]
        chunk = bytearray(stock[src:src + n])
        br = op.get("branch_relocation")
        if br:
            off, kind, tvma = (br["instruction_offset"], br["kind"],
                               br["target_vma"])
            chunk[off] = ((tvma - (0x08000000 + dst + off + 4)) // 2) & 0xFF
        t[dst:dst + n] = chunk
    t = bytes(t)
    base = 0x08000000
    for i in range(len(t)):
        old = stock[i] if i < len(stock) else 0xFF
        if t[i] != old:
            mem.w8(base + i, t[i])
    print("installed", flush=True)


m = Machine.load("cache/emu_realusb.pkl")
dev, mem = m.mem.dev, m.mem
u = dev._usb()
u.unplug()
m.cpu.run(limit=20_000_000)
disk = open_disk(ROOT)
dev.usb_disk = disk
u.stick = MassStorage(disk)
for i in range(10):
    m.cpu.run(limit=250_000_000)
    print(f"mount {i}", flush=True)


def key(k, n=15_000_000):
    dev.panel_frame = panl.build_frame(keys=[k])
    m.cpu.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=n)


key("Browse")
m.cpu.run(limit=30_000_000)
fb_png(m, "screenshots/flac_full_browse.png")
install_target(mem)
m.save("/tmp/flac_full_ready.pkl")
print("ready snapshot saved", flush=True)
