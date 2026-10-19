#!/usr/bin/env python3
"""mod: theme -- recolour the UI accent palette.

*** DO NOT FLASH THIS. USE mods/safe_theme.py INSTEAD. ***
On 2026-09-05 the accent-recolouring approach put a real XDJ-700 into MAIN CPU
ERROR: 222 direct edits, 1,099 propagated bytes. The accent ramps are exactly
the entries with the highest propagation cost, and propagation is what breaks
the deck (see NOTE_TO_DEV.txt section 2). repack.py will now refuse to build
this. Kept for reference and for simulation only.


    python3 mods/theme.py work                    # default: red
    python3 mods/theme.py work purple             # a named preset
    python3 mods/theme.py work '#FF6600'          # any colour, both accents
    python3 mods/theme.py work '#FF6600' '#00E5FF'   # warm accent, cool accent
    python3 mods/theme.py work --list             # show presets

WHY THERE IS NO ON-DECK SELECTOR
A menu on the deck would need new SH-4 code, which means recompressing block 1.
Our LZSS match semantics are provably NOT the deck's (tools/oracle.py: 100% on
the uncompressed boot stub, 12% on block 1), and our compressor is the inverse
of our own decoder -- so the deck would read anything we recompress as garbage.
Until the codec is solved, the theme has to be chosen at build time. This is
that selector.

WHAT IT TOUCHES
Nine colour tables in block 1 between 0xCC2A70 and 0xCC54E0, listed in
tools/palette.py. They do NOT share a byte order -- some store alpha in the low
byte (RGBA), some in the high byte (ARGB) -- so the order is detected per entry.
Assuming one order silently skips whole tables, which is how 0xCC2B14 (16 blues
and teals) was missed on the first pass. 94 entries total.

Only entries that are FULLY literal-encoded in the stock stream are eligible:
literals decode identically no matter how the deck resolves matches, so nothing
is recompressed and the stream length never changes.

Two accent families, classified by measured hue rather than position: WARM
(ambers/golds, ~35 deg) and COOL (blues/teals, ~210 deg). Luminance and
saturation are preserved so ramps stay ramps and contrast holds. Greys, whites
and near-blacks are left alone -- that is what makes this a re-skin and not a
mess. Use mods/probe.py if you want those repainted for identification.

NOT CONFIRMED ON HARDWARE
No colour change has ever been verified on a deck. An earlier "the border went
purple" report was a false positive -- the PERFORM button is purple in stock
firmware. Run mods/vertest.py first; it settles whether flashing works at all.
"""
import colorsys
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from litpatch import map_literals
import palette

BLK, HDR = 1, 0x40000
GREY_CUT = 0.15          # saturation below this = UI chrome, leave it
WARM_MAX = 90            # hue <90 or >300 counts as the warm accent family

# table list and byte-order handling live in tools/palette.py

# name -> (accent hue, saturation scale, label)
PRESETS = {
    "classic-blue":  (212, 1.00, "Classic Blue"),
    "sunset-orange": ( 25, 1.05, "Sunset Orange"),
    "neon-purple":   (280, 1.10, "Neon Purple"),
    "forest-green":  (130, 0.95, "Forest Green"),
    "ice-cyan":      (187, 1.05, "Ice Cyan"),
    "hot-pink":      (330, 1.10, "Hot Pink"),
    "amber-gold":    ( 45, 1.00, "Amber Gold"),
    "slate-gray":    (210, 0.12, "Slate Gray"),
    "midnight-teal": (168, 0.95, "Midnight Teal"),
    # extras
    "blood-red":     (  0, 1.05, "Blood Red"),
    "acid-lime":     ( 85, 1.15, "Acid Lime"),
    "lavender":      (265, 0.60, "Lavender"),
    "mono":          (  0, 0.00, "Mono"),
}

def parse_colour(s):
    """'#RRGGBB' -> (hue, sat_scale). Preset names are handled by the caller."""
    t = s.lstrip("#")
    if len(t) == 6:
        try:
            r, g, b = (int(t[i:i + 2], 16) / 255 for i in (0, 2, 4))
        except ValueError:
            sys.exit(f"bad colour {s!r}")
        h, _, sat = colorsys.rgb_to_hls(r, g, b)
        if sat < 0.05:
            return (0.0, 0.0)          # a grey means "desaturate"
        return (h * 360, 1.0)
    sys.exit(f"unknown preset or colour {s!r}; try --list")


def family(rgb):
    """'chrome' | 'warm' | 'cool' for one entry."""
    r, g, b = [c / 255.0 for c in rgb]
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    if s < GREY_CUT:
        return "chrome"
    deg = h * 360
    return "warm" if (deg < WARM_MAX or deg > 300) else "cool"


def restyle(rgb, hue, sat_scale, light):
    """Hue-rotate an accent, keeping its lightness; scale saturation."""
    r, g, b = [c / 255.0 for c in rgb]
    _, l, s = colorsys.rgb_to_hls(r, g, b)
    s = max(0.0, min(1.0, s * sat_scale))
    if light:
        l = min(l, 0.55)               # keep accents readable on a pale ground
    r, g, b = colorsys.hls_to_rgb((hue / 360.0) % 1.0, l, s)
    return tuple(max(0, min(255, round(c * 255))) for c in (r, g, b))


def invert_chrome(rgb):
    """Light mode: flip lightness of greys/whites/blacks. Contrast is preserved,
    so dark backgrounds become pale and light text becomes dark."""
    r, g, b = [c / 255.0 for c in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb(h, 1.0 - l, s)
    return tuple(max(0, min(255, round(c * 255))) for c in (r, g, b))


def apply(work, warm, cool, sat=1.0, light=False):
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])

    edits, counts = {}, {"warm": 0, "cool": 0, "chrome": 0}
    for o, rgb, alpha, order, base in palette.entries(dec, lit):
        fam = family(rgb)
        if fam == "chrome":
            counts["chrome"] += 1
            if not light:
                continue
            new = invert_chrome(rgb)
        else:
            hue = warm if fam == "warm" else cool
            if hue is None:
                continue
            counts[fam] += 1
            new = restyle(rgb, hue, sat, light)
        for j, byte in enumerate(palette.pack(new, alpha, order)):
            if byte != dec[o + j]:
                edits[o + j] = byte

    json.dump({str(k): v for k, v in edits.items()},
              open(f"{work}/MAIN.blk{BLK:02d}.litpatch.json", "w"))
    print(f"  warm {counts['warm']}  cool {counts['cool']}  chrome {counts['chrome']}"
          + ("  (chrome INVERTED for light mode)" if light else "  (chrome kept)"))
    print(f"  {len(edits)} bytes patched, stream length unchanged")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:]]
    light = "--light" in a
    if light:
        a.remove("--light")
    if "--list" in a:
        print("presets:")
        for k, (h, sv, lbl) in PRESETS.items():
            print(f"  {k:<15} {lbl:<15} hue {h:>3}  sat x{sv:.2f}")
        print("\nalso: any hex colour, e.g. '#FF6600'")
        print("      --light   invert greys/whites/blacks (light mode)")
        print("\nexamples:")
        print("      python3 mods/theme.py work neon-purple")
        print("      python3 mods/theme.py work ice-cyan --light")
        print("      python3 mods/theme.py work '#FF6600' '#00E5FF'")
        sys.exit(0)
    work = a[0] if a else "work"
    if len(a) > 1 and a[1] in PRESETS:
        hue, sat, lbl = PRESETS[a[1]]
        warm = cool = hue
        print(f"  preset {lbl!r}{'  + LIGHT MODE' if light else ''}")
    else:
        warm, sat = parse_colour(a[1]) if len(a) > 1 else (0.0, 1.0)
        cool = parse_colour(a[2])[0] if len(a) > 2 else warm
        print(f"  custom{'  + LIGHT MODE' if light else ''}")
    apply(work, warm, cool, sat, light)
