#!/usr/bin/env python3
"""simulate.py -- preview a theme on a real screenshot, without flashing.

    python3 tools/simulate.py SHOT.png neon-purple [--light] [-o out.png]

HOW IT WORKS, AND WHY IT IS HONEST
It does NOT draw a mock UI. It takes a real photo/screenshot of the deck's
screen, finds the pixels whose colour matches one of the 94 palette entries we
can actually patch, and recolours exactly those pixels the way the build tool
would. Pixels that do not match a palette entry are left alone, because we have
no evidence we control them.

So the output answers two questions at once:
  1. what the theme would look like
  2. HOW MUCH of the UI our palette actually covers -- printed as a coverage
     percentage. That is the number nobody has been able to measure without
     flashing, and a low number is a real (if disappointing) result.

CAVEATS
  - A photo of a screen is not the framebuffer. JPEG artefacts, backlight,
    white balance and camera gamma all shift colours, so matching needs a
    tolerance and will both miss and over-match. A clean straight-on
    screenshot beats a photo taken in a dark booth.
  - Anti-aliased edges blend palette colours with their neighbours and will
    not match.
  - Coverage is a LOWER bound on what we control, not an upper bound.
"""
import argparse, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "mods"))
from litpatch import map_literals
import palette
from theme import PRESETS, family, restyle, invert_chrome

try:
    from PIL import Image
except ImportError:
    sys.exit("needs pillow:  .venv/bin/pip install pillow")


def load_palette(work):
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    dec, lit = map_literals(img[0x40008:0x40008 + ln])
    return [(rgb, family(rgb)) for _, rgb, _, _, _ in palette.entries(dec, lit)]


def build_map(entries, hue, sat, light):
    """stock rgb -> themed rgb"""
    m = {}
    for rgb, fam in entries:
        if fam == "chrome":
            new = invert_chrome(rgb) if light else rgb
        else:
            new = restyle(rgb, hue, sat, light)
        m[rgb] = new
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shot")
    ap.add_argument("preset", nargs="?", default="neon-purple")
    ap.add_argument("--light", action="store_true")
    ap.add_argument("--tol", type=int, default=10,
                    help="per-channel match tolerance (default 10)")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--work", default=os.path.join(HERE, "..", "work"))
    a = ap.parse_args()

    if a.preset not in PRESETS:
        sys.exit(f"unknown preset {a.preset!r}; try: {', '.join(PRESETS)}")
    hue, sat, label = PRESETS[a.preset]

    entries = load_palette(a.work)
    cmap = build_map(entries, hue, sat, a.light)
    stock = list(cmap.keys())

    im = Image.open(a.shot).convert("RGB")
    px = im.load()
    w, h = im.size
    hits, changed, matched_all = {}, 0, 0
    tol = a.tol
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            for sr, sg, sb in stock:
                if abs(r - sr) <= tol and abs(g - sg) <= tol and abs(b - sb) <= tol:
                    matched_all += 1
                    nr, ng, nb = cmap[(sr, sg, sb)]
                    # keep the pixel's own deviation from the palette value,
                    # so gradients and anti-aliasing stay smooth
                    new = (max(0, min(255, nr + (r - sr))),
                           max(0, min(255, ng + (g - sg))),
                           max(0, min(255, nb + (b - sb))))
                    if new != (r, g, b):
                        changed += 1
                        hits[(sr, sg, sb)] = hits.get((sr, sg, sb), 0) + 1
                    px[x, y] = new
                    break

    total = w * h
    matched_any = matched_all
    out = a.out or f"sim_{a.preset}{'_light' if a.light else ''}.png"
    im.save(out)

    print(f"  preset      : {label}{'  + light mode' if a.light else ''}")
    print(f"  image       : {w}x{h}  ({total:,} px)")
    print(f"  matched     : {matched_any:,} px sit on a palette colour "
          f"({matched_any*100/total:.1f}%)")
    print(f"  CHANGED     : {changed:,} px actually differ  "
          f"({changed*100/total:.1f}%)   <-- what you would SEE")
    print(f"  palette entries that visibly change: {len(hits)} of {len(stock)}")
    if hits:
        top = sorted(hits.items(), key=lambda kv: -kv[1])[:8]
        print("  most-used stock colours:")
        for (r, g, b), n in top:
            print(f"    #{r:02X}{g:02X}{b:02X}  {n:>7,} px -> "
                  f"#{cmap[(r,g,b)][0]:02X}{cmap[(r,g,b)][1]:02X}{cmap[(r,g,b)][2]:02X}")
    else:
        print("  NOTHING MATCHED. Either the shot's colours are shifted (photo of a"
              "\n  screen), or our palette genuinely does not drive this screen."
              "\n  Try --tol 20, or use a straight-on screenshot.")
    print(f"  written     : {out}")


if __name__ == "__main__":
    main()
