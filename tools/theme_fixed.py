#!/usr/bin/env python3
"""theme_fixed.py -- integer (deck-computable) version of the themes.py
recolour. A theme = 4 params (hue, spread, sat, val); the deck computes the
1154 destination colours itself from the fixed source list, so a theme
menu only needs 8 bytes per theme on the deck.

Fixed point: hue h6 in 0..1535 (6 sectors x 256), s Q8 (0..256), v 0..255,
divisions via recip[m] = 65535 // m (the table already in the waveform
cave). `compare` reports the error vs the float reference per theme.
"""
import sys
import numpy as np
sys.path[:0] = [".", "tools", "mods"]

RECIP = [0] + [65535 // m for m in range(1, 256)]
MID6 = round((190.0 + 260.0) / 2 / 60 * 256)       # 225 deg -> 960


def div(x, d):                     # x * 256 / d using the reciprocal table
    neg = x < 0
    q = (abs(x) * 256 * RECIP[d]) >> 16
    return -q if neg else q


def params(spec):
    return (round(spec["hue"] / 60 * 256) % 1536, round(spec["spread"] * 256),
            round(spec["sat"] * 256), round(spec["val"] * 256))


def fixed_colour(c, prm):
    H0, sp, sa, va = prm
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        return c
    if mx == r:
        h = div(g - b, d) % 1536
    elif mx == g:
        h = div(b - r, d) + 512
    else:
        h = div(r - g, d) + 1024
    s = (d * 256 * RECIP[mx]) >> 16
    v = mx
    nh = (H0 + (((h - MID6) * sp) >> 8)) % 1536
    ns = min(256, (s * sa) >> 8)
    nv = min(255, (v * va) >> 8)
    i, f = nh >> 8, nh & 255
    p = (nv * (256 - ns)) >> 8
    q = (nv * (65536 - ns * f)) >> 16
    t = (nv * (65536 - ns * (256 - f))) >> 16
    R, G, B = [(nv, t, p), (q, nv, p), (p, nv, t), (p, q, nv), (t, p, nv),
               (nv, p, q)][i]
    return ((R >> 3) << 11) | ((G >> 2) << 5) | (B >> 3)


def compare():
    import themes as T
    import theme_deck as TD
    w = TD.stock_words()
    worst = 0
    for name, spec in T.THEMES.items():
        if spec is None:
            continue
        _, src, dst, _ = TD.theme_tables(name, w)
        prm = params(spec)
        errs = []
        for c, ref in zip(src.tolist(), dst.tolist()):
            f = fixed_colour(c, prm)
            e = max(abs(((f >> 11) & 31) - ((ref >> 11) & 31)),
                    abs(((f >> 5) & 63) - ((ref >> 5) & 63)) / 2,
                    abs((f & 31) - (ref & 31)))
            errs.append(e)
        errs = np.array(errs)
        worst = max(worst, errs.max())
        print(f"{name:14s} max err {errs.max():.1f} LSB5, exact "
              f"{(errs == 0).mean() * 100:.0f}%, >1 LSB {(errs > 1).sum()}")
    print("worst", worst)


if __name__ == "__main__":
    compare()
