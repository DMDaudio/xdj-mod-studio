#!/usr/bin/env python3
"""browse_seek.py -- step the emulated browse knob until the highlighted
row matches a reference crop (420x30 PNG of a highlighted row), or show
every highlighted row it passes. Knob detents must be consumed one at a
time (fast multi-detent turns accelerate/merge, like the real knob).

  CKPT=... browse_seek.py DIR MAXSTEPS [REF.png] [SAVE.pkl]
  DIR = + or -. Writes /tmp/seek_rows.png (montage of rows seen).
"""
import os
import sys
import numpy as np
from PIL import Image
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

ROT = panl.ROTARY_COUNTER


def highlighted(path):
    im = Image.open(path).convert("RGB")
    for k in range(7):
        y = 107 + 30 * k
        if sum(im.getpixel((450, y + 5))) > 600:
            return im.crop((80, y, 500, y + 30))
    return None


def main():
    d = 1 if sys.argv[1] == "+" else -1
    n = int(sys.argv[2])
    ref = np.asarray(Image.open(sys.argv[3]).convert("L"), float) \
        if len(sys.argv) > 3 and sys.argv[3] != "-" else None
    save = sys.argv[4] if len(sys.argv) > 4 else None
    m = Machine.load(os.environ["CKPT"])
    m.mem.dev.panel_frame = panl.build_frame()
    rows = []
    hit = False
    for i in range(n):
        m.mem.w16(ROT, (m.mem.r16(ROT) + d) & 0xFFFF)
        m.cpu.run(limit=15_000_000)
        fb_png(m, "/tmp/seek.png")
        h = highlighted("/tmp/seek.png")
        if h is None:
            continue
        rows.append(h)
        if ref is not None:
            diff = np.abs(np.asarray(h.convert("L"), float) - ref).mean()
            if diff < 4:
                print(f"match after {i + 1} steps (diff {diff:.1f})")
                hit = True
                break
    W = Image.new("RGB", (420, 30 * max(1, len(rows))))
    for i, r in enumerate(rows):
        W.paste(r, (0, 30 * i))
    W.save("/tmp/seek_rows.png")
    if save and (hit or ref is None):
        m.save(save)
        print("saved", save)
    if ref is not None and not hit:
        print("no match")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
