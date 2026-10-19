#!/usr/bin/env python3
"""theme_bij.py -- REVERSIBLE themes (switch theme <-> stock <-> theme on
the running deck, no artwork backup).

Why the old themes were one-way: a theme maps ~1183 stock source colours
onto far fewer colours, so the original of a pixel can't be recovered.
Fix: make every theme a BIJECTION from the shared source list S onto its
own destination set D_t:
  * dst = theme_fixed.fixed_colour(src) (integer HSV, deck-computable),
  * if that colour is already taken -- by an earlier source of this theme
    or by a "forbidden" colour (present in the artwork regions but not a
    source) -- step to the nearest free colour in a fixed search order
    (visually 1-2 LSB away).
Then on the deck:  stock -> T : pixel s -> D_T[idx(s)]
                   T -> stock : pixel d -> S[idx_T(d)]
                   T -> U     : d -> D_U[idx_T(d)]
Every pixel holding a colour not in the current set is left untouched,
which is exact because D_t never contains a forbidden colour.

  theme_bij.py check   (python model: all themes bijective, round trips
                        T -> stock == stock bit-exact, A -> B == stock -> B,
                        colour error vs the float reference)
"""
import sys

import numpy as np

sys.path[:0] = [".", "tools", "mods"]
import theme_fixed as TF

# search order for a free colour: small RGB565 steps first
STEPS = [(dr, dg, db) for d in range(1, 4)
         for dr in range(-d, d + 1) for dg in range(-d, d + 1)
         for db in range(-d, d + 1)
         if max(abs(dr), abs(dg), abs(db)) == d]


def _rgb(c):
    return (c >> 11) & 31, (c >> 5) & 63, c & 31


def _pack(r, g, b):
    return (r << 11) | (g << 5) | b


def sources_and_forbidden(w, regions):
    """S = colours any theme changes (sorted); F = other colours that occur
    inside the artwork regions (must never be produced)."""
    import themes as T
    import theme_deck as TD
    srcs = set()
    for name, spec in T.THEMES.items():
        if spec is None:
            continue
        _, src, _, _ = TD.theme_tables(name, w)
        srcs |= set(src.tolist())
    present = set()
    for a, n in regions:
        present |= set(np.unique(w[a:a + n]).tolist())
    S = sorted(srcs)
    F = present - srcs
    return S, F


def theme_map(S, F, spec):
    prm = TF.params(spec)
    used = set(F)
    D = []
    for s in S:
        c = TF.fixed_colour(s, prm)
        if c in used:
            r, g, b = _rgb(c)
            for dr, dg, db in STEPS:
                rr, gg, bb = r + dr, g + dg, b + db
                if 0 <= rr < 32 and 0 <= gg < 64 and 0 <= bb < 32:
                    c2 = _pack(rr, gg, bb)
                    if c2 not in used:
                        c = c2
                        break
            else:
                raise RuntimeError("no free colour")
        used.add(c)
        D.append(c)
    return D


def apply(w, regions, frm, to):
    """Recolour inside regions: colours in `frm` -> same index in `to`."""
    lut = np.arange(65536, dtype=np.uint16)
    lut[np.array(frm)] = np.array(to, dtype=np.uint16)
    out = w.copy()
    for a, n in regions:
        out[a:a + n] = lut[w[a:a + n]]
    return out


def regions_of(w):
    import themes as T
    art, nb = T.art_windows(w)
    heur = art & ~T.text_windows(w, nb)
    pic = np.repeat(heur, T.WIN) | T.proven_sprites(len(w))[:nb * T.WIN]
    pic = np.pad(pic, (0, max(0, len(w) - len(pic))))[:len(w)]
    d = np.diff(np.concatenate([[0], pic.astype(np.int8), [0]]))
    st, en = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return list(zip(st.tolist(), (en - st).tolist()))


def check():
    import themes as T
    import theme_deck as TD
    w = TD.stock_words()
    regions = regions_of(w)
    S, F = sources_and_forbidden(w, regions)
    print(f"sources {len(S)}, forbidden {len(F)}, regions {len(regions)}")
    maps = {}
    ok = True
    for name, spec in T.THEMES.items():
        if spec is None:
            continue
        D = theme_map(S, F, spec)
        assert len(set(D)) == len(D) and not (set(D) & F)
        maps[name] = D
        # colour error vs the float reference (what the old theme showed)
        _, src, dst, _ = TD.theme_tables(name, w)
        ref = dict(zip(src.tolist(), dst.tolist()))
        err = 0
        for s, d in zip(S, D):
            if s in ref:
                a, b = _rgb(d), _rgb(ref[s])
                err = max(err, abs(a[0] - b[0]), abs(a[1] - b[1]) / 2,
                          abs(a[2] - b[2]))
        themed = apply(w, regions, S, D)
        back = apply(themed, regions, D, S)
        exact = np.array_equal(back, w)
        ok &= exact
        print(f"{name:14s} max colour error {err:.1f} LSB  "
              f"theme->stock exact: {exact}")
    names = list(maps)
    a, b = names[0], names[8]
    via = apply(apply(w, regions, S, maps[a]), regions, maps[a], maps[b])
    direct = apply(w, regions, S, maps[b])
    sw = np.array_equal(via, direct)
    print(f"switch {a} -> {b} == stock -> {b}: {sw}")
    ok &= sw
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check())
