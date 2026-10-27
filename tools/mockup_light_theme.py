#!/usr/bin/env python3
"""mockup_light_theme.py -- design mockup of a LIGHT deck theme (not deck
code), from a real emulator main screen:
  ideal     : every pixel's lightness flipped (hue kept), waveform colours
              kept -- what it would look like if the deck's own text could
              be recoloured too
  realistic : same, but the pixels our theme system can't reach today stay
              as they are: the near-white text the firmware draws itself
              (track title, time, BPM ...) -> white on light = unreadable
-> screenshots/mockups/light_theme.png"""
import colorsys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SRC = "screenshots/phasemeter_dsp_3.png"
F = "/System/Library/Fonts/HelveticaNeue.ttc"


def flip(a, keep):
    out = a.copy()
    h, w, _ = a.shape
    for y in range(h):
        for x in range(w):
            if keep[y, x]:
                continue
            r, g, b = a[y, x] / 255.0
            hh, l, s = colorsys.rgb_to_hls(r, g, b)
            l2 = 0.96 - 0.82 * l                     # dark bg -> light grey
            out[y, x] = np.array(colorsys.hls_to_rgb(hh, l2, s * 0.8)) * 255
    return out


def main():
    a = np.array(Image.open(SRC).convert("RGB")).astype(float)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    sat = (np.max(a, 2) - np.min(a, 2)) / 255.0
    wave = np.zeros(r.shape, bool)
    wave[165:315, 110:620] = sat[165:315, 110:620] > 0.25      # coloured waveform
    wave[405:450, 110:620] = sat[405:450, 110:620] > 0.25
    ideal = flip(a, wave)
    text = (r > 200) & (g > 200) & (b > 200)                   # firmware text
    real = flip(a, wave | text)
    W, H = 800, 480
    out = Image.new("RGB", (W, 3 * H + 150), (12, 12, 16))
    d = ImageDraw.Draw(out)
    f = ImageFont.truetype(F, 20, index=1)
    for i, (title, img) in enumerate((("Today (stock dark)", a),
                                      ("Light theme - ideal (deck text recoloured too)", ideal),
                                      ("Light theme - what themes can do today (white deck text stays white)", real))):
        d.text((16, 10 + i * (H + 50)), title, font=f, fill=(235, 238, 242))
        out.paste(Image.fromarray(img.clip(0, 255).astype(np.uint8)), (0, 40 + i * (H + 50)))
    out.save("screenshots/mockups/light_theme.png")
    print("screenshots/mockups/light_theme.png")


if __name__ == "__main__":
    main()
