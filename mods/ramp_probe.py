#!/usr/bin/env python3
"""mod: ramp_probe -- repaint every literal RGB565 colour ramp, one hue per family.

    python3 mods/ramp_probe.py work

WHY THIS AND NOT ANOTHER PALETTE EDIT
build/probe_green_v2.05 proved two things on hardware: payload edits are LIVE
(the block checksum was the whole brick mechanism), and the 32-bit table at
0xCC2A70 is NOT the live palette -- #2C2C2C occurs exactly once as a 32-bit
constant in 18.4 MB, we changed it, and the 8.07% of screen painted that colour
did not move.

RGB565 is the panel format. tools/ramps.py finds 21 literal RGB565 ramps in 10
distinct patterns: orange/amber ramps (cue markers), long blue ramps (waveform),
and a 9-entry blue-grey gradient repeated 13 times (panel/button shading). That
is what an XDJ-700 palette should look like.

ONE FLASH, ONE PHOTO, FIVE ANSWERS
Each family gets a different unmistakable hue, so a single photo says which UI
element each ramp paints. Luminance is preserved, so gradients still read as
gradients rather than flat blocks -- which also keeps text legible.

    orange/amber ramps  -> MAGENTA
    long blue ramps     -> GREEN
    repeated 9e blues   -> RED
    teal ramp           -> YELLOW
    grey ramp           -> CYAN

Magenta is deliberate: nothing on the stock XDJ-700 is magenta. The PERFORM
button is purple in STOCK firmware and already caused one false positive in
this project, so the probe hues are chosen to be unambiguous.

CODEC NOTE
Offsets printed here are from OUR decoder and are NOT where these bytes live in
the deck's memory -- output position depends on the unsolved match-length
formula. That does not matter: we patch literal STREAM bytes, which is
codec-independent, so wherever a ramp lives it gets recoloured. We simply cannot
predict in advance which element moves. Hence the probe.
"""
import json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from litpatch import map_literals
from ramps import find, from565, to565, family

HDR = 0x40000
HUES = {"orange/amber": ("MAGENTA", (1, 0, 1)),
        "blue":         ("GREEN",   (0, 1, 0)),
        "blue9":        ("RED",     (1, 0, 0)),
        "teal/cyan":    ("YELLOW",  (1, 1, 0)),
        "grey":         ("CYAN",    (0, 1, 1)),
        "green":        ("YELLOW",  (1, 1, 0)),
        "other":        ("CYAN",    (0, 1, 1))}


def fanout(src):
    """-> {stream_offset: how many output bytes descend from this literal}"""
    import collections
    N, F, THR, FILL = 4096, 18, 2, 0x20
    rb = bytearray([FILL] * (N - F) + [0] * F); rroot = [-1] * N
    fan = collections.Counter(); r, p, n = N - F, 0, len(src)
    while p < n:
        fl = src[p]; p += 1
        for b in range(8):
            if p >= n: break
            if fl >> b & 1:
                c = src[p]; rb[r] = c; rroot[r] = p; fan[p] += 1
                p += 1; r = (r + 1) % N
            else:
                if p + 1 >= n: break
                b1, b2 = src[p], src[p + 1]; p += 2
                i = b2 | ((b1 & 0xF0) << 4); j = (b1 & 0x0F) + THR + 1
                for k in range(j):
                    q = (i + k) % N; rb[r] = rb[q]; rroot[r] = rroot[q]
                    if rroot[q] >= 0: fan[rroot[q]] += 1
                    r = (r + 1) % N
    return fan


def apply(work="work", budget=None):
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    ramps = find(dec, lit, 8)

    # Select by measured propagation, cheapest first, keeping one ramp per
    # family so a single photo still maps every family. Propagation is not the
    # brick mechanism (BOOTSTUB.md) but a match CAN copy an edited byte into a
    # non-colour context, so we spend the budget deliberately.
    if budget is not None:
        fan = fanout(img[HDR + 8:HDR + 8 + ln])
        cost = []
        for off, vals in ramps:
            c = sum((fan.get(lit[off + t], 1) - 1)
                    for t in range(len(vals) * 2) if off + t in lit)
            cost.append((c, off, vals))
        cost.sort()
        # Family coverage FIRST -- the cheapest ramp of each family -- then
        # spend whatever is left on the next cheapest. Filling greedily by cost
        # alone eats the budget on cheap blues and never reaches the orange
        # ramps, which are the most recognisable target on screen (cue markers).
        chosen, seen, spent = [], set(), 0
        def key_of(vals):
            f = family(vals)
            return "blue9" if (f == "blue" and len(vals) == 9) else f
        for c, off, vals in cost:
            k = key_of(vals)
            if k not in seen:
                chosen.append((off, vals)); seen.add(k); spent += c
        for c, off, vals in cost:
            if (off, vals) in chosen: continue
            if spent + c <= budget:
                chosen.append((off, vals)); spent += c
        ramps = chosen
        print(f"  selected {len(ramps)} of {len(cost)} ramps, "
              f"{spent} propagated bytes (budget {budget})")

    p = f"{work}/MAIN.blk01.litpatch.json"
    edits = json.load(open(p)) if os.path.exists(p) else {}
    n = 0
    tally = {}
    for off, vals in ramps:
        fam = family(vals)
        key = "blue9" if (fam == "blue" and len(vals) == 9) else fam
        name, (kr, kg, kb) = HUES[key]
        for t, v in enumerate(vals):
            r, g, b = from565(v)
            L = (r * 299 + g * 587 + b * 114) // 1000
            nv = to565(kr * L, kg * L, kb * L)
            o = off + t * 2
            for k, byte in ((0, nv & 0xFF), (1, nv >> 8)):
                if o + k not in lit:
                    sys.exit(f"non-literal at {o + k:#x} -- refusing")
                if dec[o + k] != byte:
                    edits[str(o + k)] = byte; n += 1
        tally[name] = tally.get(name, 0) + len(vals)

    json.dump(edits, open(p, "w"))
    print(f"  {len(ramps)} ramps repainted, {n} literal bytes edited")
    for k, v in sorted(tally.items()):
        print(f"    {k:<8} {v:>4} entries")
    print("  photograph the whole screen after flashing; each hue names a ramp")


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "work",
          int(sys.argv[2]) if len(sys.argv) > 2 else None)
