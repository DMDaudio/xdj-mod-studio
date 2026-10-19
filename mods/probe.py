#!/usr/bin/env python3
"""mod: probe -- map every palette table to a UI element in ONE flash.

    python3 mods/probe.py work

Each group below gets an unmistakable hue. Photograph the deck afterwards and
the colour on screen names the table that painted it.

    MAGENTA  0xCC2AC0 cool half   <- control: already confirmed = panel border
    RED      0xCC2B14             blue + teal ramp  (waveform suspect)
    ORANGE   0xCC2A70             amber ramp
    YELLOW   0xCC2AC0 warm half   gold ramp
    GREEN    0xCC5368             mid greys
    CYAN     0xCC53F8             near-blacks (backgrounds)
    BLUE     0xCC5288/52A8/52E0   whites
    VIOLET   0xCC54D8             very dark

This is a DIAGNOSTIC, not a theme -- it repaints greys and backgrounds too, so
it will look awful. Lightness is preserved, so text stays legible.
Literal-only: nothing recompressed, stream length unchanged.
"""
import colorsys, json, os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from litpatch import map_literals
import palette

BLK, HDR = 1, 0x40000
WARM_MAX, GREY_CUT, CHROME_SAT = 90, 0.15, 0.55

HUE = {0xCC2B14: 0, 0xCC2A70: 35, 0xCC5368: 120, 0xCC53F8: 180,
       0xCC5288: 240, 0xCC52A8: 240, 0xCC52E0: 240, 0xCC54D8: 270}
SPLIT = {0xCC2AC0: {"warm": 60, "cool": 300}}
NAME = {0:"RED",35:"ORANGE",60:"YELLOW",120:"GREEN",180:"CYAN",240:"BLUE",
        270:"VIOLET",300:"MAGENTA"}


def apply(work):
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    edits, tally = {}, {}
    for o, rgb, alpha, order, base in palette.entries(dec, lit):
        r, g, b = [c / 255.0 for c in rgb]
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        chrome = s < GREY_CUT
        if base in SPLIT:
            d = h * 360
            fam = "chrome" if chrome else ("warm" if (d < WARM_MAX or d > 300) else "cool")
            hue = SPLIT[base].get(fam)
            if hue is None:
                continue
        else:
            hue = HUE[base]
        if chrome:
            l, s = max(l, 0.12), CHROME_SAT
        nr, ng, nb = colorsys.hls_to_rgb((hue / 360.0) % 1.0, l, s)
        new = tuple(max(0, min(255, round(c * 255))) for c in (nr, ng, nb))
        for j, byte in enumerate(palette.pack(new, alpha, order)):
            if byte != dec[o + j]:
                edits[o + j] = byte
        tally[hue] = tally.get(hue, 0) + 1
    json.dump({str(k): v for k, v in edits.items()},
              open(f"{work}/MAIN.blk{BLK:02d}.litpatch.json", "w"))
    for hue in sorted(tally):
        print(f"  {NAME.get(hue,hue):<8} {tally[hue]:>2} entries")
    print(f"  {sum(tally.values())} entries repainted, {len(edits)} bytes, length unchanged")


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "work")
