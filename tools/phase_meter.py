#!/usr/bin/env python3
"""phase_meter.py -- Type 1 (lines) / Type 2 (blocks) phase meter prototype.

Drawn on the real main-screen layout with deck colours. Phases are
SIMULATED (own phase + a drifting partner) until the link beat-phase
hunt lands real data. Pure Studio-side preview for now; the deck cave
comes after the data hunt.

Usage: .venv/bin/python tools/phase_meter.py [--type 1|2|both] [--phase 0.0..1]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

WHITE = 0xFFFF
GREY = 0x8410
CYAN = 0x07FF
MAGENTA = 0xF81F

# meter geometry: phase-meter band between time readout and overview
MX, MW, MY, MH = 150, 500, 370, 34

ORANGE = 0xE4A5   # deck orange (3-band mid)
BLUE = 0x1B1A     # deck blue (3-band high)
RED = 0xF800


def base_fb():
    z = np.load(os.path.join(ROOT, "studio", "assets", "screens.npz"))
    return z["main"].copy()


def draw_type1(fb, own, other, bar1=(88, 8), bar2=(88, 8)):
    """CDJ Type 1: orange top row / blue bottom row, beat ticks, white
    center, red position markers, MASTER CLOCK label, bar counters."""
    from PIL import Image, ImageDraw, ImageFont
    fb[MY:MY + MH, MX:MX + MW] = 0x0000
    y1, y2 = MY + 9, MY + MH - 9
    # beat ticks (8 per row) + row lines
    for i in range(8):
        x = MX + 40 + i * (MW - 80) // 7
        fb[y1 - 7:y1 + 8, x - 1:x + 1] = GREY
        fb[y2 - 7:y2 + 8, x - 1:x + 1] = GREY
    fb[y1 - 1:y1 + 2, MX + 30:MX + MW - 90] = ORANGE
    fb[y2 - 1:y2 + 2, MX + 30:MX + MW - 90] = BLUE
    # white center
    cx = MX + 30 + MW // 2 - 60
    fb[MY + 4:MY + MH - 4, cx - 1:cx + 2] = WHITE
    # red markers from beat phase
    w = MW // 2 - 70
    for y, ph in ((y1, own), (y2, other)):
        x = int(cx + (ph - 0.5) * 2 * w)
        fb[y - 9:y + 10, x - 2:x + 3] = RED
    # text: MASTER CLOCK left, bar counters right (drawn in RGB)
    a = fb.astype(np.uint32)
    rgb = np.dstack([((a >> 11) & 31) * 255 // 31,
                     ((a >> 5) & 63) * 255 // 63,
                     (a & 31) * 255 // 31]).astype(np.uint8)
    im = Image.fromarray(rgb)
    dr = ImageDraw.Draw(im)
    try:
        fbig = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        fsm = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
    except OSError:
        fbig, fsm = ImageFont.load_default(), ImageFont.load_default()
    dr.text((MX + 2, y1 - 12), "MASTER", font=fsm, fill=(230, 149, 41))
    dr.text((MX + 2, y1 + 2), "CLOCK", font=fsm, fill=(230, 149, 41))
    dr.text((MX + MW - 88, y1 - 14), f"{bar1[0]}.{bar1[1]}", font=fbig,
            fill=(230, 149, 41))
    dr.text((MX + MW - 88, y2 - 2), f"{bar2[0]}.{bar2[1]}", font=fbig,
            fill=(24, 97, 213))
    return np.array(im)


def draw_type2(fb, own, other, nblocks=15):
    """Row of blocks; lit span between own and partner positions."""
    fb[MY:MY + MH, MX:MX + MW] = 0x0000
    bw = MW // nblocks
    a = int(own * (nblocks - 1))
    b = int(other * (nblocks - 1))
    lo, hi = min(a, b), max(a, b)
    for i in range(nblocks):
        x0 = MX + i * bw + 2
        on = lo <= i <= hi
        col = CYAN if on else 0x2104
        fb[MY + 6:MY + MH - 6, x0:x0 + bw - 4] = col
    fb[MY + MH // 2 - 1:MY + MH // 2 + 1, MX:MX + MW] = GREY
    return fb


def save(fb, path):
    from PIL import Image
    a = fb.astype(np.uint32)
    rgb = np.dstack([((a >> 11) & 31) * 255 // 31,
                     ((a >> 5) & 63) * 255 // 63,
                     (a & 31) * 255 // 31]).astype(np.uint8)
    Image.fromarray(rgb).save(path)
    print("saved", path)


def save_rgb(im, path):
    from PIL import Image
    Image.fromarray(np.array(im)).save(path)
    print("saved", path)


# ---------------------------------------------------------------------------
# Type 1 on the XDJ-700's OWN meter box (replaces the stock 4-box rows).
# Reference: XDJ-1000MK2 photo (2026-10-11): orange = master, blue = this
# deck; each row is a ruler of beats scrolling past a fixed white "now"
# line; grey ticks = beats, red ticks = bar downbeats (every 4 beats);
# in phase when the red ticks of both rows line up. Orange ticks hang
# below its line, blue ticks stand above its line.
T1_X0, T1_X1 = 260, 471          # stock box rows span (measured on the emu)
T1_CX = 366                      # = waveform playhead column
T1_BEATS = 8                     # +-4 beats visible
T1_PPB = (T1_X1 - T1_X0) / T1_BEATS
T1_OY, T1_BY = 124, 150          # orange / blue line rows
T1_ORANGE = (232, 141, 33)
T1_ORANGE_DIM = (90, 55, 15)
T1_BLUE = (36, 139, 228)
T1_TICK = (150, 150, 150)
T1_RED = (226, 40, 40)
T1_WHITE = (255, 255, 255)


def type1_marks(beat_pos):
    """(x, is_downbeat) for every beat inside the window. beat_pos = beats
    since the grid's first downbeat (float), so beat k is a downbeat when
    k % 4 == 0."""
    import math
    out = []
    lo = math.floor(beat_pos - T1_BEATS / 2) - 1
    for k in range(lo, lo + T1_BEATS + 3):
        x = T1_CX + (k - beat_pos) * T1_PPB
        if T1_X0 <= x <= T1_X1:
            out.append((int(round(x)), k % 4 == 0))
    return out


def draw_type1_700(img, own, master=None):
    """img: PIL RGB image of the 800x480 screen (modified in place).
    own / master: beat positions (float beats) or None (no data)."""
    from PIL import ImageDraw, ImageFont
    d = ImageDraw.Draw(img)
    d.rectangle((T1_X0 - 2, 116, T1_X1 + 2, 158), fill=(0, 0, 0))  # stock boxes off
    for y, col, pos, down in ((T1_OY, T1_ORANGE, master, True),
                              (T1_BY, T1_BLUE, own, False)):
        d.line((T1_X0, y, T1_X1, y), fill=col if pos is not None else
               (T1_ORANGE_DIM if down else (15, 55, 90)), width=2)
        if pos is None:
            continue
        for x, bar in type1_marks(pos):
            if bar:                     # red downbeat: crosses the line
                d.rectangle((x - 1, y - 8, x, y + 8), fill=T1_RED)
            elif down:                  # grey beat tick below the orange line
                d.rectangle((x, y + 2, x, y + 8), fill=T1_TICK)
            else:                       # ... above the blue line
                d.rectangle((x, y - 8, x, y - 2), fill=T1_TICK)
    d.rectangle((T1_CX - 1, 115, T1_CX + 1, 159), fill=T1_WHITE)   # now
    try:
        f = ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", 11, index=1)
    except OSError:
        f = ImageFont.load_default()
    d.text((178, 120), "MASTER", font=f, fill=T1_ORANGE)
    d.text((182, 132), "CLOCK", font=f, fill=T1_ORANGE)
    cx, cy, r = 238, 132, 9                          # hexagon emblem
    import math
    pts = [(cx + r * math.cos(math.radians(60 * i + 30)),
            cy + r * math.sin(math.radians(60 * i + 30))) for i in range(6)]
    d.polygon(pts, outline=T1_ORANGE, width=2)
    d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), outline=T1_ORANGE, width=2)
    return img


def demo_700(src, out, own=37.35, master=37.0):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    draw_type1_700(im, own, master)
    im.save(out)
    print("saved", out)


def main():
    typ = sys.argv[sys.argv.index("--type") + 1] if "--type" in sys.argv else "both"
    ph = float(sys.argv[sys.argv.index("--phase") + 1]) if "--phase" in sys.argv else 0.35
    other = (ph + 0.5) % 1.0
    if typ in ("1", "both"):
        save_rgb(draw_type1(base_fb(), ph, other), "screenshots/phase_type1.png")
    if "--on" in sys.argv:          # --on SCREEN.png OUT.png: 700 layout
        i = sys.argv.index("--on")
        demo_700(sys.argv[i + 1], sys.argv[i + 2], ph * 4 + 36, 36.0)
        return
    if typ in ("2", "both"):
        save(draw_type2(base_fb(), ph, other), "screenshots/phase_type2.png")


if __name__ == "__main__":
    main()
