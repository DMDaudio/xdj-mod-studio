#!/usr/bin/env python3
"""theme_rev.py -- reversible on-deck themes, exact model of the deck code.

The deck computes everything itself (no colour lists over the slow USB
shell): only the artwork REGION list, the code and 4 numbers per theme are
installed.
  init (first switch, artwork still stock):
    PB = bitmap of every colour present in the regions
    S  = present colours the themes recolour (integer HSV selection:
         hue in [HLO, HHI], sat >= SMIN, not a key colour), ascending
    F  = PB minus S  (colours a theme may never produce)
  compute(t): D[i] = fixed_colour(S[i], params_t), nudged to the nearest
    free colour (search order STEPS) if already used (F or earlier D)
  switch CUR -> TARGET: map FROM (S or D_CUR) -> TO (S or D_TARGET),
    sorted by FROM, then the stock region recolour pass.
Every theme is a bijection S -> D_t with D_t disjoint from F, so stock ->
T -> stock and A -> B are exact.

  theme_rev.py check
"""
import sys

import numpy as np

sys.path[:0] = [".", "tools", "mods"]
import theme_fixed as TF

HLO = round(190.0 / 60 * 256)      # 811
HHI = round(260.0 / 60 * 256)      # 1109
SMIN = round(0.16 * 256)           # 41
KEYS = (0xF81F, 0x07E0, 0x0000, 0xFFFF)
R8 = [x * 255 // 31 for x in range(32)]
G8 = [x * 255 // 63 for x in range(64)]


def hue_sat(c):
    """Integer hue (0..1535) and sat (Q8) exactly as the deck computes."""
    r, g, b = R8[(c >> 11) & 31], G8[(c >> 5) & 63], R8[c & 31]
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        return 0, 0
    if mx == r:
        h = TF.div(g - b, d) % 1536
    elif mx == g:
        h = TF.div(b - r, d) + 512
    else:
        h = TF.div(r - g, d) + 1024
    s = (d * 256 * TF.RECIP[mx]) >> 16
    return h, s


def selected(c):
    if c in KEYS:
        return False
    h, s = hue_sat(c)
    return HLO <= h <= HHI and s >= SMIN


def steps():
    out = []
    for d in range(1, 4):
        for dr in range(-d, d + 1):
            for dg in range(-d, d + 1):
                for db in range(-d, d + 1):
                    if max(abs(dr), abs(dg), abs(db)) == d:
                        out.append((dr, dg, db))
    return out


STEPS = steps()


def init(w, regions):
    pb = np.zeros(65536, bool)
    for a, n in regions:
        pb[np.unique(w[a:a + n])] = True
    S = [c for c in range(65536) if pb[c] and selected(c)]
    F = pb.copy()
    F[S] = False
    return S, F


def compute(S, F, prm):
    used = F.copy()
    D = []
    for s in S:
        c = TF.fixed_colour(s, prm)
        if used[c]:
            r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
            for dr, dg, db in STEPS:
                rr, gg, bb = r + dr, g + dg, b + db
                if 0 <= rr < 32 and 0 <= gg < 64 and 0 <= bb < 32:
                    c2 = (rr << 11) | (gg << 5) | bb
                    if not used[c2]:
                        c = c2
                        break
            else:
                raise RuntimeError("no free colour")
        used[c] = True
        D.append(c)
    return D


def apply(w, regions, frm, to):
    lut = np.arange(65536, dtype=np.uint16)
    lut[np.array(frm)] = np.array(to, dtype=np.uint16)
    out = w.copy()
    for a, n in regions:
        out[a:a + n] = lut[w[a:a + n]]
    return out


def theme_list():
    import themes as T
    return [n for n, s in T.THEMES.items() if s is not None]


def theme_params():
    import themes as T
    return [TF.params(T.THEMES[n]) for n in theme_list()]


def check():
    import theme_deck as TD
    import theme_bij as TB
    w = TD.stock_words()
    regions = TB.regions_of(w)
    S, F = init(w, regions)
    print(f"S {len(S)} colours, F {int(F.sum())}, regions {len(regions)}")
    Ds = [compute(S, F, p) for p in theme_params()]
    ok = True
    for name, D in zip(theme_list(), Ds):
        assert len(set(D)) == len(D) and not F[np.array(D)].any()
        t = apply(w, regions, S, D)
        ok &= np.array_equal(apply(t, regions, D, S), w)
    a, b = Ds[0], Ds[8]
    ok &= np.array_equal(apply(apply(w, regions, S, a), regions, a, b),
                         apply(w, regions, S, b))
    # how close to the original (one-way) themes
    _, src, dst, new = TD.theme_tables("hot-pink", w)
    t = apply(w, regions, S, Ds[theme_list().index("hot-pink")])
    same = int((t == new).sum() - (w == new).sum() + (w == new).sum())
    diff = int((t != new).sum())
    print(f"hot-pink: {diff:,} px differ from the old one-way theme "
          f"(colour steps of 1-4 LSB / borderline selections)")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check())
