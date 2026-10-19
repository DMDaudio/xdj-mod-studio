"""PURPLE skin for the XDJ-700.

Rotates blue-dominant pixels toward purple inside regions that are provably
bitmap data. Everything else -- code, strings, tables, text pixels, neutrals,
and the 0xF81F / 0x07E0 transparency keys -- is left byte-identical.

Requires the correct codec (tools/lzss_boot.py). Under the old, wrong decode
this edit would have been meaningless.
"""
import numpy as np

WIN   = 1024          # u16 per window
KEYS  = (0xF81F, 0x07E0, 0x0000, 0xFFFF)
# SH-4 prologue/epilogue opcodes -- if a window has these, it is code, not art
CODE  = (0x4F22, 0x4F26, 0x2FE6, 0x6EF6, 0x2FD6, 0x6DF6, 0x000B)
AMOUNT, DESAT = 0.62, 0.80


def _blue_mask(v):
    r = ((v >> 11) & 0x1F) * 255 // 31
    g = ((v >>  5) & 0x3F) * 255 // 63
    b = ( v        & 0x1F) * 255 // 31
    m = (b > r + 10) & (b >= g)
    for k in KEYS:
        m &= (v != k)
    return m, r, g, b


def apply(buf, hdr):
    if hdr != 0x40000:
        return buf
    n = len(buf) // 2 * 2
    w = np.frombuffer(bytes(buf[:n]), dtype='<u2').copy()
    nb = len(w) // WIN
    w2 = w[:nb * WIN].reshape(nb, WIN)

    flat = (w2[:, 1:] == w2[:, :-1]).mean(1)          # bitmap smoothness
    mag  = (w2 == 0xF81F).mean(1)                      # transparency key
    code = np.zeros(nb, bool)
    for op in CODE:
        code |= (w2 == op).any(1)
    bm, _, _, _ = _blue_mask(w2.astype(np.uint32))
    bluefrac = bm.mean(1)

    art = ((flat > 0.35) | (mag > 0.02)) & ~code & (bluefrac > 0.05)
    sel = np.repeat(art, WIN)

    v = w[:nb * WIN].astype(np.uint32)
    m, r, g, b = _blue_mask(v)
    m &= sel
    nr = np.where(m, np.minimum(255, r + (b - r) * AMOUNT), r).astype(np.uint32)
    ng = np.where(m, np.minimum(255, g * DESAT), g).astype(np.uint32)
    out = ((nr >> 3) << 11) | ((ng >> 2) << 5) | (b.astype(np.uint32) >> 3)
    w[:nb * WIN] = out.astype(np.uint16)

    res = bytearray(buf)
    res[:n] = w.tobytes()
    print(f"      art windows {int(art.sum()):,}/{nb:,}  "
          f"pixels recoloured {int(m.sum()):,}")
    return res
