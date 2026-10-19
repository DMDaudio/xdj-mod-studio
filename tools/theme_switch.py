#!/usr/bin/env python3
"""theme_switch.py -- on-deck theme SWITCHING (MY SETTINGS [THEME] cycles
OFF -> theme 1 -> ... -> OFF, live, any order, exact).

Tables are built here with the SAME colour maths as the Studio preview
(themes.map_colours, incl. vivid/lift), then made one-to-one: each theme
maps the artwork source colours S onto distinct colours D_t that never
occur elsewhere in the artwork (nudged 1-4 LSB when two would collide).
So stock -> A -> B -> stock returns every pixel exactly. Deck code:
tools/themesw/ts.c (built with tools/themesw/build.sh).

  theme_switch.py check            Python model: exactness + preview match
  theme_switch.py plan [themes..]  sizes / addresses for the deck
  theme_switch.py emu  [themes..]  emulator: real MY SETTINGS pushes
  theme_switch.py export [themes..] build/themesw/ files for the emu app
"""
import os
import struct
import subprocess
import sys

import numpy as np

sys.path[:0] = [".", "tools", "mods"]
import theme_rev as TR

IMG_BASE = 0x08000000
TS_BASE = int(os.environ.get("TS_BASE", "0x09770000"), 0)
TS_DATA = int(os.environ.get("TS_DATA", "0x09776000"), 0)
TS_END = TS_BASE + 0x20000                 # 0x09790000: hot cues follow
# never recolour our own code/data caves (waveform, versions, settings,
# PERFORM pads): CAVE .. end of the PERFORM zero run
NO_TOUCH = (0x081D8900, 0x081E0400)
MAGIC = 0x57535458                         # "XTSW"
CHUNK = 256                                # px per safety fingerprint
# The firmware writes a few words INSIDE real artwork at runtime
# (emulator: 8 KB-stride headers + play-HISTORY text at 0x087E0000..
# 0x087FC00C, in the middle of the LINK/USB button art). No blanket
# exclusion (that left LINK unthemed): the 128-px stock fingerprint skips
# exactly the touched blocks, on the deck too.
LIVE_DATA = []


def _steps(radius=8):
    """neighbour offsets, nearest first (green has 6 bits = half weight)"""
    out = []
    for dr in range(-radius, radius + 1):
        for dg in range(-2 * radius, 2 * radius + 1):
            for db in range(-radius, radius + 1):
                if dr or dg or db:
                    out.append((max(abs(dr), abs(dg) / 2, abs(db)),
                                abs(dr) + abs(dg) / 2 + abs(db), dr, dg, db))
    out.sort()
    return [o[2:] for o in out]


STEPS = _steps()


# Our own PERFORM pads (tools/perform_pads.py) carry a copy of the stock
# hot-cue pad art and four blue pool colours: added as extra regions so they
# follow the theme too (2026-10-13). On the deck the per-chunk stock
# fingerprint skips them when the pads are not installed (RAM = zero there).
OWN_COLOURS = ("DIM", "KEDGE", "KFILL", "KPRESS")


def own_art():
    """-> ([(addr, bytes)] pad blobs, [(addr, n_px)] extra regions)"""
    import perform_pads as PP
    blobs = PP.blobs()
    regs = [(PP.PADART, PP.PADART_DATA.size)]
    want = {getattr(PP, n) for n in OWN_COLOURS}
    for base, b in blobs:
        if base in (PP.PADART,) or base >= PP.FONTP:
            continue
        for o in range(0, len(b) - 3, 4):
            if (base + o) % 4:
                continue
            v = struct.unpack_from("<I", b, o)[0]
            if v in want:                   # pool literal 0x0000CCCC
                regs.append((base + o, 1))  # low halfword = the colour
    return blobs, regs


def with_own_art(w):
    """stock words + our pad blobs written into the (zero) cave"""
    w = w.copy()
    for base, b in own_art()[0]:
        s = (base - IMG_BASE) // 2
        w[s:s + len(b) // 2] = np.frombuffer(b[:len(b) // 2 * 2], "<u2")
    return w


def regions(w):
    import theme_bij as TB
    out = []
    spans = [(IMG_BASE + 2 * s, IMG_BASE + 2 * (s + n))
             for s, n in TB.regions_of(w)]
    for lo, hi in [NO_TOUCH] + LIVE_DATA:
        cut = []
        for a, b in spans:
            for x, y in ((a, min(b, lo)), (max(a, hi), b)):
                if y > x:
                    cut.append((x, y))
        spans = cut
    spans = _fill_gaps(w, spans)
    return [(a, (b - a) // 2) for a, b in spans] + own_art()[1]


GAP_WIN = 1024          # px
GAP_DENSITY = 0.90      # share of themable colours that marks a window as art


def _fill_gaps(w, spans):
    """2026-10-18: the artwork detector leaves holes inside sprite sheets
    (deck photo: the 'hot cue set' band on pad A stayed stock blue; source
    sprite at 0x0840C000.., between detected regions). Add every GAP_WIN-px
    window that is >= 90 % themable colours and not already covered (never
    inside NO_TOUCH). The deck's per-chunk stock fingerprint still guards."""
    import themes as T
    lo_img, hi_img = min(a for a, _ in spans), max(b for _, b in spans)
    allc = np.arange(65536, dtype=np.uint16)
    themable = T.map_colours(allc, T.THEMES["crimson-red"]) != allc
    cov = np.zeros((hi_img - lo_img) // 2, bool)
    for a, b in spans:
        cov[(a - lo_img) // 2:(b - lo_img) // 2] = True
    nt0, nt1 = NO_TOUCH
    add = []
    s0 = (lo_img - IMG_BASE) // 2
    for k in range(0, len(cov) - GAP_WIN + 1, GAP_WIN):
        if cov[k:k + GAP_WIN].all():
            continue
        a = lo_img + 2 * k
        if a + 2 * GAP_WIN > nt0 and a < nt1:
            continue
        seg = w[s0 + k:s0 + k + GAP_WIN]
        if themable[seg].mean() >= GAP_DENSITY:
            add.append((a, a + 2 * GAP_WIN))
    out = sorted(spans + add)
    merged = []
    for a, b in out:                       # union (keeps every pixel once)
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def fingerprints(w, regs, S):
    """-> (themable-chunk bitmap, u32 FNV-1a per themable chunk) over CHUNK
    px of every region in stock colours -- identical to ts.c recolour()"""
    inS = np.zeros(65536, bool)
    inS[S] = True
    bits, out = [], []
    for a, n in regs:
        s = (a - IMG_BASE) // 2
        for k in range(0, n, CHUNK):
            v = w[s + k:s + min(n, k + CHUNK)]
            if not inS[v].any():
                bits.append(0)
                continue
            bits.append(1)
            h = 0x811C9DC5
            for x in v.tolist():
                h = ((h ^ x) * 0x01000193) & 0xFFFFFFFF
            out.append(h)
    return (np.packbits(np.array(bits, np.uint8), bitorder="little").tobytes(),
            np.array(out, "<u4").tobytes())


def present_colours(w, regs):
    """-> (present mask, pixel count per colour) inside the regions"""
    cnt = np.zeros(65536, np.int64)
    for a, n in regs:
        s = (a - IMG_BASE) // 2
        cnt += np.bincount(w[s:s + n], minlength=65536)
    return cnt > 0, cnt


def tables(names, w=None):
    """-> regs, S (sorted), [D_t], forbidden mask"""
    import themes as T
    import theme_deck as TD
    w = with_own_art(TD.stock_words()) if w is None else w
    regs = regions(w)
    pb, cnt = present_colours(w, regs)
    cols = np.nonzero(pb)[0].astype(np.uint16)
    changed = np.zeros(len(cols), bool)
    maps = {}
    for n in names:
        m = T.map_colours(cols, T.THEMES[n])
        maps[n] = m
        changed |= m != cols
    S = cols[changed]
    F = pb.copy()
    F[S] = False
    # most-used colours claim their exact target first; rarer ones get
    # nudged (keeps the visible difference to the preview tiny)
    order = np.argsort(-cnt[S], kind="stable")
    Ds = []
    for n in names:
        want = maps[n][changed]
        used = F.copy()
        D = np.zeros(len(S), np.uint16)
        for k in order.tolist():
            c = int(want[k])
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
                    raise RuntimeError(f"{n}: no free colour")
            used[c] = True
            D[k] = c
        Ds.append(D)
    return regs, S, Ds, F


def blob(names, w=None):
    import theme_deck as TD
    w = with_own_art(TD.stock_words()) if w is None else w
    regs, S, Ds, _ = tables(names, w)
    ns, nt = len(S), len(names)
    reg_off = 56
    s_off = reg_off + 8 * len(regs)
    d_off = s_off + 2 * ns
    name_off = d_off + 2 * ns * nt
    name_off += (-name_off) % 4
    tbits, hashes = fingerprints(w, regs, S)
    tbits_off = name_off + 16 * nt
    hash_off = tbits_off + len(tbits)
    hash_off += (-hash_off) % 4
    out = struct.pack("<8I4B5I", MAGIC, len(regs), ns, nt, reg_off, s_off,
                      d_off, name_off, 0, 0, 0, 0, 0, hash_off, CHUNK, 0,
                      tbits_off)
    out += bytes(reg_off - len(out))
    for a, n in regs:
        out += struct.pack("<II", a, n)
    out += S.astype("<u2").tobytes()
    for D in Ds:
        out += D.astype("<u2").tobytes()
    out += bytes(name_off - len(out))
    for n in names:
        out += n.upper().encode()[:15].ljust(16, b"\0")
    assert len(out) == tbits_off
    out += tbits
    return out + bytes(hash_off - len(out)) + hashes


def model_switch(img, regs, frm, to):
    """exactly what ts.c does (per-pixel FROM -> TO inside the regions)"""
    lut = np.arange(65536, dtype=np.uint16)
    lut[np.asarray(frm)] = np.asarray(to, np.uint16)
    out = img.copy()
    for a, n in regs:
        s = (a - IMG_BASE) // 2
        out[s:s + n] = lut[img[s:s + n]]
    return out


def default_names():
    import themes as T
    return [n for n, s in T.THEMES.items() if s is not None]


def check(names):
    import themes as T
    import theme_deck as TD
    w = with_own_art(TD.stock_words())
    regs, S, Ds, F = tables(names, w)
    own = np.zeros(len(w), bool)                 # our pads: not in the preview
    for a, n in own_art()[1]:
        own[(a - IMG_BASE) // 2:(a - IMG_BASE) // 2 + n] = True
    b = blob(names, w)
    npx = sum(n for _, n in regs)
    print(f"{len(names)} themes, S {len(S)} colours, {len(regs)} regions, "
          f"{npx / 1e6:.2f} M px, blob {len(b) / 1024:.1f} KB")
    ok = TS_DATA + len(b) <= TS_END
    for D in Ds:
        ok &= len(set(D.tolist())) == len(D) and not F[D].any()
    # cycle stock -> every theme -> stock, in order and some jumps
    cur, img = 0, w.copy()
    seq = list(range(1, len(names) + 1)) + [0, len(names), 2, 0]
    for t in seq:
        frm = S if cur == 0 else Ds[cur - 1]
        to = S if t == 0 else Ds[t - 1]
        img = model_switch(img, regs, frm, to)
        direct = model_switch(w, regs, S, to)
        ok &= np.array_equal(img, direct)
        cur = t
    ok &= np.array_equal(img, w)
    print("stock -> all themes -> jumps -> stock exact:", np.array_equal(img, w))
    # how close each theme is to the Studio preview (themes.recolour)
    worst = 0.0
    for n, D in zip(names, Ds):
        ref = T.recolour(w.copy(), T.THEMES[n])[0]
        got = model_switch(w, regs, S, D)
        ch = (ref != w) & ~own
        bad = (got != ref) & ch & ~own     # where the reference themes too
        extra = (got != w) & ~ch & ~own    # gap-filled art (2026-10-18)
        g, r = got[bad].astype(int), ref[bad].astype(int)
        e = np.maximum(np.maximum(abs((g >> 11) - (r >> 11)),
                                  abs(((g >> 5) & 63) - ((r >> 5) & 63)) // 2),
                       abs((g & 31) - (r & 31))) if bad.any() else np.zeros(1, int)
        # 2026-10-18: calmer themes = closer colours = more bijection nudges;
        # judge what is VISIBLE: share of themed pixels off by > 3/31
        far = int((e > 3).sum()) if bad.any() else 0
        frac = far / max(1, int(ch.sum()))
        worst = max(worst, frac)
        print(f"  {n:14s} {int(ch.sum()):>9,} px themed; vs Studio preview: "
              f"{int(bad.sum()):>7,} px off (max {int(e.max())} LSB), "
              f"{far:,} px > 3 LSB ({100 * frac:.3f} %), mean {e.sum() / max(1, ch.sum()):.2f}; "
              f"+{int(extra.sum()):,} px gap-filled art")
    ok &= worst <= 0.002   # <= 0.2 % of themed pixels visibly different
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def build_code():
    import mysettings as MS
    env = dict(os.environ, TS_BASE=hex(TS_BASE), TS_DATA=hex(TS_DATA),
               MSTNAME=hex(MS.MSTNAME), MSTAPP=hex(MS.MSTAPP))
    import shbuild
    shbuild.build("tools/themesw/build.sh",
                  ["build/themesw/ts.bin", "build/themesw/ts.syms"], env)
    code = open("build/themesw/ts.bin", "rb").read()
    syms = {}
    for ln in open("build/themesw/ts.syms"):
        a, _, s = ln.split()
        syms[s] = int(a, 16)
    assert syms["_ts_table"] == TS_BASE
    return code, syms


def plan(names):
    """[(addr, bytes)] for the deck (code + data), table at TS_BASE"""
    code, _ = build_code()
    data = blob(names)
    assert TS_BASE + len(code) <= TS_DATA and TS_DATA + len(data) <= TS_END
    return [(TS_BASE, code), (TS_DATA, data)]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    names = sys.argv[2:] or default_names()
    if cmd == "check":
        return check(names)
    if cmd == "plan":
        for a, b in plan(names):
            print(f"{a:#010x} {len(b):6d} B")
        print(f"survey window {TS_BASE:#x}..{TS_END:#x}")
        return 0
    if cmd == "export":                  # prebuilt files for the emulator app
        (cb, code), (db, data) = plan(names)
        open("build/themesw/tables.bin", "wb").write(data)
        open("build/themesw/layout.txt", "w").write(
            f"{cb:#x} {db:#x} {' '.join(names)}\n")
        print(f"build/themesw/ts.bin {len(code)} B @ {cb:#x}, tables.bin "
              f"{len(data)} B @ {db:#x}, {len(names)} themes")
        return 0
    if cmd == "emu":
        import mysettings as MS
        return MS.emu_theme_switch(names)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
