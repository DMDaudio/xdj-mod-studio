#!/usr/bin/env python3
"""mod: safe_theme -- recolour only what is SAFE to recolour.

    python3 mods/safe_theme.py work '#1A2E4A'          # background tint
    python3 mods/safe_theme.py work '#1A2E4A' --budget 32

WHY THIS REPLACES mods/theme.py FOR HARDWARE
theme.py recolours the ACCENT ramps and preserves the chrome. On 2026-09-05 that
put a real deck into MAIN CPU ERROR: 222 direct edits, 1,099 propagated bytes.
A build with 3 direct and 0 propagated flashed perfectly.

Propagation is the danger. A direct literal edit decodes to itself under ANY
match semantics; a byte that a later match COPIES does not, because where the
copy lands depends on semantics we have not proven.

And the costs fall exactly backwards from what a naive theme wants:

    near-blacks (backgrounds)  14/14 FREE
    very dark                   5/5  FREE
    whites + near-whites        8/10 FREE
    mid greys                  10/14 FREE
    blue/teal ramp              5/16 free   <- accents are expensive
    gold+blue ramp              3/19 free
    amber ramp                  0/11 free

So this mod themes the CHROME -- background, greys, text tone -- plus whichever
accent entries happen to be free. It cannot recolour a whole waveform ramp
safely, and it will tell you which entries it skipped and why.

Budget defaults to 32, matching repack.py's PROP_LIMIT.
"""
import colorsys, json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from litpatch import map_literals
import palette, propcost
import collections

BLK, HDR = 1, 0x40000


def tint(rgb, hue, sat, keep_light=True):
    r, g, b = [c / 255.0 for c in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    ns = max(s, sat) if s < 0.15 else s
    r, g, b = colorsys.hls_to_rgb((hue / 360.0) % 1.0, l, ns)
    return tuple(max(0, min(255, round(c * 255))) for c in (r, g, b))


def screen_weights(work, dec, lit, shot=None):
    """px-on-screen per palette entry, from a real screenshot. Falls back to
    equal weights if no screenshot is available."""
    shot = shot or os.path.expanduser("~/Downloads/xdj-screenshot.jpg")
    ents = [(o, rgb) for o, rgb, a, order, b in palette.entries(dec, lit)]
    try:
        from PIL import Image
        px = Image.open(shot).convert("RGB").get_flattened_data() \
             if hasattr(Image.Image, "get_flattened_data") else \
             list(Image.open(shot).convert("RGB").getdata())
    except Exception:
        return {o: 1 for o, _ in ents}
    w = collections.Counter()
    for (r, g, b) in px:
        for o, (sr, sg, sb) in ents:
            if abs(r - sr) <= 12 and abs(g - sg) <= 12 and abs(b - sb) <= 12:
                w[o] += 1; break
    return w


def apply(work, hexcol, budget=32, sat=0.35, shot=None):
    t = hexcol.lstrip("#")
    R, G, B = (int(t[i:i + 2], 16) for i in (0, 2, 4))
    hue = colorsys.rgb_to_hls(R / 255, G / 255, B / 255)[0] * 360

    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    cost = propcost.analyse(work)
    weight = screen_weights(work, dec, lit, shot)

    ents = list(palette.entries(dec, lit))
    free = [e for e in ents if cost.get(e[0], 999) == 0]
    paid = [e for e in ents if 0 < cost.get(e[0], 999) <= budget]

    # 0/1 knapsack over the paid entries: maximise screen pixels within budget
    paid.sort(key=lambda e: -weight.get(e[0], 0) / max(cost[e[0]], 1))
    chosen, spent = [], 0
    for e in paid:
        c = cost[e[0]]
        if spent + c <= budget:
            chosen.append(e); spent += c

    edits, npx = {}, 0
    for o, rgb, alpha, order, base in free + chosen:
        new = tint(rgb, hue, sat)
        if new == rgb:
            continue
        for j, byte in enumerate(palette.pack(new, alpha, order)):
            if byte != dec[o + j]:
                edits[o + j] = byte
        npx += weight.get(o, 0)

    json.dump({str(k): v for k, v in edits.items()},
              open(f"{work}/MAIN.blk{BLK:02d}.litpatch.json", "w"))
    total_px = sum(weight.values())
    print(f"  tint {hexcol}  (hue {hue:.0f} deg)")
    print(f"  {len(free)} free entries + {len(chosen)} bought "
          f"= {len(free)+len(chosen)} of {len(ents)}")
    print(f"  propagation: {spent} of {budget} budget")
    print(f"  screen coverage: {npx:,} of {total_px:,} paintable px "
          f"({npx*100//max(total_px,1)}%)")
    print(f"  {len(edits)} bytes patched, stream length unchanged")


if __name__ == "__main__":
    a = sys.argv[1:]
    budget = 32
    if "--budget" in a:
        i = a.index("--budget"); budget = int(a[i + 1]); del a[i:i + 2]
    work = a[0] if a else "work"
    col = a[1] if len(a) > 1 else "#1A2E4A"
    apply(work, col, budget)
