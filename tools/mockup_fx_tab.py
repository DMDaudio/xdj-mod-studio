#!/usr/bin/env python3
"""mockup_fx_tab.py -- design mockup of a PERFORM "FX" tab (not deck code).
Draws over a real emulator PERFORM screen: 5th tab FX, effect picker in the
BEAT LOOP row, an X-pad style touch strip in the hot-cue row.
-> screenshots/mockups/fx_tab.png (two states: ECHO, FILTER)"""
from PIL import Image, ImageDraw, ImageFont

BASE = "screenshots/perform_pads_beatjump.png"
F = "/System/Library/Fonts/HelveticaNeue.ttc"
f = lambda s, i=0: ImageFont.truetype(F, s, index=i)
BOLD, COND = 1, 7
ORANGE, ORANGE_D = (240, 140, 30), (120, 66, 10)
PAD_BG, PAD_EDGE = (38, 44, 54), (78, 92, 110)
WHITE, GREY, DIM = (235, 238, 242), (150, 158, 170), (90, 96, 106)
FX = ["ECHO", "FILTER", "ROLL", "CRUSH", "REVERB", "GATE"]
PADS = [(116, 79), (201, 79), (285, 79), (369, 79), (453, 79), (537, 79)]


def pad(d, x, y, w, h, on):
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=PAD_BG,
                outline=ORANGE if on else PAD_EDGE)
    d.rectangle([x + 6, y + 4, x + w - 7, y + 6], fill=ORANGE if on else (55, 62, 74))


def ctext(d, cx, cy, s, font, col):
    b = d.textbbox((0, 0), s, font=font)
    d.text((cx - (b[2] - b[0]) / 2 - b[0], cy - (b[3] - b[1]) / 2 - b[1]), s, font=font, fill=col)


def screen(mode):
    im = Image.open(BASE).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rectangle([12, 140, 633, 302], fill=(0, 0, 0))          # clear our area
    # tabs (5, condensed)
    tabs = ["HOT CUE", "BEAT JUMP", "KEY SHIFT", "STEMS", "FX"]
    for i, t in enumerate(tabs):
        y = 232 + 13 * i
        on = t == "FX"
        d.rectangle([16, y, 107, y + 11], fill=(225, 225, 225) if on else (52, 58, 68),
                    outline=(120, 128, 140))
        ctext(d, 62, y + 6, t, f(9, BOLD), (20, 20, 20) if on else GREY)
    # FX picker (BEAT LOOP row)
    ctext(d, 52, 180, "FX TYPE", f(10, BOLD), GREY)
    d.line([82, 180, 110, 180], fill=DIM)
    sel = 0 if mode == "echo" else 1
    for i, ((x, w), n) in enumerate(zip(PADS, FX)):
        pad(d, x, 154, w, 53, i == sel)
        ctext(d, x + w / 2, 186, n, f(14, BOLD), WHITE if i == sel else GREY)
    # X-pad strip (hot-cue row)
    x0, x1, y0, y1 = 116, 615, 232, 295
    d.rectangle([x0, y0, x1, y1], fill=(16, 20, 28), outline=PAD_EDGE)
    for gx in range(x0 + 6, x1 - 3, 7):                       # dot grid
        for gy in range(y0 + 8, y1 - 4, 7):
            d.point((gx, gy), fill=(48, 58, 74))
    if mode == "echo":
        stops = ["1/8", "1/4", "1/2", "3/4", "1", "2"]
        finger = 2
        n = len(stops)
        seg = (x1 - x0) / n
        for i, s in enumerate(stops):
            cx = x0 + seg * (i + 0.5)
            if i:
                d.line([x0 + seg * i, y0 + 4, x0 + seg * i, y1 - 4], fill=(60, 70, 86))
            on = i == finger
            if on:                                            # finger glow
                d.rectangle([x0 + seg * i + 2, y0 + 2, x0 + seg * (i + 1) - 2, y1 - 2],
                            fill=ORANGE_D, outline=ORANGE)
            ctext(d, cx, 268, s, f(17, BOLD), WHITE if on else GREY)
        ctext(d, x0 + seg * 2.5, 246, "BEAT", f(9, BOLD), (255, 210, 160))
        readout = "ECHO  1/2 BEAT"
    else:
        cx = (x0 + x1) / 2
        d.line([cx, y0 + 4, cx, y1 - 4], fill=(110, 120, 136))
        fx = x0 + 120                                         # finger: LPF 52 %
        for x in range(int(fx), int(cx)):
            a = (x - fx) / (cx - fx)
            c = tuple(int(ORANGE_D[k] * (1 - a) + 16 * a) for k in range(3))
            d.line([x, y0 + 2, x, y1 - 2], fill=c)
        d.line([fx, y0 + 2, fx, y1 - 2], fill=ORANGE, width=3)
        ctext(d, x0 + 44, 280, "LPF", f(12, BOLD), WHITE)
        d.polygon([(x0 + 12, 280), (x0 + 22, 274), (x0 + 22, 286)], fill=WHITE)
        ctext(d, x1 - 44, 280, "HPF", f(12, BOLD), GREY)
        d.polygon([(x1 - 12, 280), (x1 - 22, 274), (x1 - 22, 286)], fill=GREY)
        ctext(d, cx, 280, "OFF", f(10, BOLD), GREY)
        readout = "FILTER  LPF 52%"
    d.rectangle([x1 - 120, y0 + 3, x1 - 4, y0 + 17], fill=(0, 0, 0))
    ctext(d, x1 - 62, y0 + 10, readout, f(10, BOLD), ORANGE)
    d.rectangle([x0 + 4, y0 + 3, x0 + 168, y0 + 17], fill=(0, 0, 0))
    ctext(d, x0 + 86, y0 + 10, "TOUCH & SLIDE · LIFT = OFF", f(9, BOLD), GREY)
    return im


def main():
    a, b = screen("echo"), screen("filter")
    W, H = a.size
    out = Image.new("RGB", (W, 2 * H + 90), (12, 12, 16))
    d = ImageDraw.Draw(out)
    d.text((16, 10), "FX tab — ECHO selected, finger on 1/2 beat", font=f(20, BOLD), fill=WHITE)
    out.paste(a, (0, 40))
    d.text((16, H + 50), "FX tab — FILTER selected, finger sweeping low-pass", font=f(20, BOLD), fill=WHITE)
    out.paste(b, (0, H + 80))
    out.save("screenshots/mockups/fx_tab.png")
    print("screenshots/mockups/fx_tab.png")


if __name__ == "__main__":
    main()
