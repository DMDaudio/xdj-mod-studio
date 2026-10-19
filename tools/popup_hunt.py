#!/usr/bin/env python3
"""popup_hunt.py -- fresh boot WITH stick, SCSI-error logging, popup check.

Boots the firmware from reset (C core) with the FolderDisk attached,
logging every SCSI command + non-zero status with step counts. After
~420M steps: fb screenshot (is the popup there?), then a "Back" key
press via the panel frame, screenshot again (did it dismiss?).
"""
import os
import sys

os.environ["XDJ_USB"] = "/tmp/usbstick"

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import numpy as np
from PIL import Image
from emu.machine import Machine
from emu import fw as F
from emu import panl

FB_W, FB_H = 800, 480
EVENTS = []


def fb_png(m, path):
    reg = m.mem.dev.reg
    base = sum(reg.get(0xFE940430 + i, 0) << (8 * i) for i in range(4))
    idx = 1 if (base & 0x1FFFFFFF) == 0x0E83B800 else 0
    buf = bytes(m.mem.fb[idx])
    if not any(buf[::97]):
        buf = bytes(m.mem.fb[1 - idx])
    a = np.frombuffer(buf, dtype="<u2").astype(np.uint32)
    r = ((a >> 11) & 0x1F); g = ((a >> 5) & 0x3F); b = (a & 0x1F)
    rgb = np.dstack([
        ((r * 255 + 15) // 31).astype(np.uint8).reshape(FB_H, FB_W),
        ((g * 255 + 31) // 63).astype(np.uint8).reshape(FB_H, FB_W),
        ((b * 255 + 15) // 31).astype(np.uint8).reshape(FB_H, FB_W)])
    Image.fromarray(rgb).save(path)
    return buf


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 420_000_000
    flash, _ = F.load_flash(None)
    m = Machine(flash)                    # C core
    dev = m.mem.dev
    wired = [False]

    def wire():
        if wired[0]:
            return
        usb = getattr(dev, "usb", None)
        stick = getattr(usb, "stick", None) if usb else None
        if stick is None:
            return
        wired[0] = True
        orig_status = stick._status
        orig_scsi = stick._scsi

        def status(st, residue=0, *a, **k):
            if st != 0:
                EVENTS.append(("ERR", st, tuple(stick.sense), m.cpu.steps))
            return orig_status(st, residue, *a, **k)

        def scsi(cb, dlen, flags, *a, **k):
            EVENTS.append(("op", hex(cb[0]), dlen, m.cpu.steps))
            return orig_scsi(cb, dlen, flags, *a, **k)
        stick._status = status
        stick._scsi = scsi
        print("scsi instrumentation wired")

    chunk = 60_000_000
    done = 0
    while done < total:
        m.cpu.run(limit=chunk)
        done += chunk
        wire()
        usb = getattr(dev, "usb", None)
        errs = [e for e in EVENTS if e[0] == "ERR"]
        print(f"steps={m.cpu.steps/1e6:6.0f}M "
              f"attached={usb.attached if usb else '-'} "
              f"scsi_ops={sum(1 for e in EVENTS if e[0]=='op')} "
              f"errors={len(errs)}", flush=True)

    fb0 = fb_png(m, "screenshots/popup_boot_end.png")
    print("scsi op histogram:")
    from collections import Counter
    ops = Counter(e[1] for e in EVENTS if e[0] == "op")
    print("  ", dict(ops))
    errs = [e for e in EVENTS if e[0] == "ERR"]
    print(f"non-zero statuses: {len(errs)}")
    for e in errs[:15]:
        print("   ", e)
    if not errs:
        print("   (none -- transport clean)")

    # press Back via panel frame, see if popup dismisses
    fr = bytearray(panl.build_frame())
    p = bytearray(fr[:30]); p[20] |= 0x10    # Back
    out = bytearray(p); out.append(panl.checksum(p)); out.append(0x8F)
    dev.panel_frame = bytes(out)
    m.cpu.run(limit=8_000_000)
    dev.panel_frame = panl.build_frame()     # release
    m.cpu.run(limit=8_000_000)
    fb1 = fb_png(m, "screenshots/popup_after_back.png")
    diff = sum(1 for x, y in zip(fb0, fb1) if x != y)
    print(f"after Back press: fbdiff={diff}")


if __name__ == "__main__":
    main()
