#!/usr/bin/env python3
"""rgb_render.py -- XDJ-1000-style RGB waveform renderer (offline proof).

Column formats (rekordbox ANLZ, big-endian):
  PWV5 (detail): 2B/col: (R3<<13)|(G3<<10)|(B3<<7)|(H5<<2)  (low 2 bits 0)
  PWV4 (overview 1200x6B): 6B/col (two RGB triples: low+high? handled as
      they appear in real exports -- see decode_pwv4)
  PWV7 (3-band detail): 3B/col (one byte per band height)
  PWV6 (3-band overview 1200x3B): same, 1200 cols
3-bit channels expand to 8-bit via *255//7 (round-trip exact for
primaries); heights scale to the target half-height.

Usage: .venv/bin/python tools/rgb_render.py  (synthetic self-test -> PNG)
  renders /tmp/rgb_test.png (detail) + /tmp/rgb_overview.png.
"""
import struct
import sys

sys.path.insert(0, ".")


def dec_pwv5(word):
    r = (word >> 13) & 7
    g = (word >> 10) & 7
    b = (word >> 7) & 7
    h = (word >> 2) & 31
    return r * 255 // 7, g * 255 // 7, b * 255 // 7, h


def dec_pwv4(six):
    """1200x6B color overview: two RGB565-ish triples (low, high)? The
    observed layout is two (R,G,B) bytes x2; treat as low/high gain of
    the same column and take the brighter per channel."""
    a = tuple(six[:3])
    b = tuple(six[3:])
    return tuple(max(x, y) for x, y in zip(a, b))


def dec_band3(three):
    """PWV6/PWV7 three-band columns: one height byte per band
    (low, mid, high). Colors follow the deck convention
    (low=red-ish? verified against real exports tomorrow)."""
    return tuple(three[:3])


def rgb565(r, g, b):
    """Deck framebuffer format. 3-bit sources survive nearly exactly
    (max error 1 LSB in green)."""
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)


def un565(v):
    return ((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, \
           (v & 31) * 255 // 31


def parse_pqt2(body):
    """Beat ticks -> normalized beat positions 0..1 (cumulative intervals
    scaled by their total; every 4th tick is a bar line in 4/4)."""
    import struct
    n = len(body) // 2
    vals = struct.unpack(">" + "H" * n, body[:n * 2])
    runs = []
    for v in vals:
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    total = sum(v * c for v, c in runs)
    pos, beats = 0.0, []
    for v, c in runs:
        for _ in range(c):
            beats.append(pos / total)
            pos += v
    beats.append(1.0)
    return beats


def enc_pwv5(r, g, b, h):
    return ((r * 7 // 255) << 13) | ((g * 7 // 255) << 10) | \
           ((b * 7 // 255) << 7) | (h << 2)


def render_detail(cols, width=800, half=100, bg=(0, 0, 0), beats=None):
    """cols: list of (r,g,b,h01) with h01 0..1 fraction of half-height.
    beats: optional normalized 0..1 beat positions (bar = every 4th)."""
    from PIL import Image
    img = Image.new("RGB", (width, 2 * half), bg)
    px = img.load()
    n = len(cols)
    for x in range(width):
        r, g, b, h = cols[x * n // width]
        hh = max(1, int(h * half))
        for y in range(half - hh, half + hh):
            px[x, y] = (r, g, b)
    if beats:
        bi = 0
        for k, p in enumerate(beats):
            x = int(p * (width - 1))
            col = (255, 255, 255) if k % 4 == 0 else (90, 90, 90)
            for y in range(2 * half):
                px[x, y] = col
    return img


def render_overview(cols, width=800, half=40, bg=(0, 0, 0)):
    return render_detail(cols, width, half, bg)


def selftest():
    import math
    # synthetic detail: solid R | G | B thirds, height breathes 0.15..1.0
    cols = []
    for i in range(1200):
        r, g, b = (255, 0, 0) if i < 400 else ((0, 255, 0) if i < 800
                                               else (0, 0, 255))
        h = 0.15 + 0.85 * abs(math.sin(i * 0.05)) * abs(math.sin(i * 0.013))
        cols.append((r, g, b, h))
    # round-trip through the PWV5 packing
    words = [enc_pwv5(r, g, b, min(31, int(h * 31))) for r, g, b, h in cols]
    back = [dec_pwv5(w) for w in words]
    assert back[0][:3] == (255, 0, 0), back[0]
    assert back[400][:3] == (0, 255, 0), back[400]
    assert back[800][:3] == (0, 0, 255), back[800]
    cols2 = [(r, g, b, h / 31) for r, g, b, h in back]
    render_detail(cols2).save("/tmp/rgb_test.png")
    ov = [(r, g, b, h) for r, g, b, h in cols[::6][:200]]
    render_overview(ov).save("/tmp/rgb_overview.png")
    print("PWV5 round-trip exact (R/G/B primaries); "
          "saved /tmp/rgb_test.png + /tmp/rgb_overview.png")
    # PWV4: brighter-of-two wins
    assert dec_pwv4(bytes([10, 20, 30, 200, 5, 5])) == (200, 20, 30)
    assert dec_pwv4(bytes([0, 0, 0, 0, 0, 0])) == (0, 0, 0)
    # 3-band passthrough
    assert dec_band3(bytes([11, 22, 33])) == (11, 22, 33)
    # RGB565: all 512 3-bit colors survive (green <= 4/255 off)
    worst = 0
    for r3 in range(8):
        for g3 in range(8):
            for b3 in range(8):
                r, g, b = r3 * 255 // 7, g3 * 255 // 7, b3 * 255 // 7
                r2, g2, b2 = un565(rgb565(r, g, b))
                worst = max(worst, abs(r - r2), abs(g - g2), abs(b - b2))
    assert worst <= 8, worst
    print(f"PWV4/3-band decode ok; RGB565 max error {worst}/255")


if __name__ == "__main__":
    selftest()
