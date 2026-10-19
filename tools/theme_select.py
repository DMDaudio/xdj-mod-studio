#!/usr/bin/env python3
"""XDJ-700 theme selector.

    theme_select.py list
    theme_select.py preview STOCK.UPD [outdir]     render every theme
    theme_select.py build   STOCK.UPD THEME [out]  write a themed .UPD

Selection happens here, at build time, not on the deck. That is not a
shortcut -- see SKIN.md: the deck's UI art is direct-colour RGB565 bitmaps,
not palettised, so there is no palette to swap at runtime.
"""
import os, sys, struct
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mods'))
import lzss_boot as L
from fwtool import parse, assemble
from themes import THEMES, recolour

SPOTS = [(0x080f274, 144, 110), (0x051f000, 150, 90), (0x0450000, 120, 90)]


def _blk1(secs):
    m = secs[0]
    for h, ln in m.blocks():
        if h == 0x40000:
            return m, h, ln
    raise SystemExit('block1 not found')


def cmd_list():
    for k in THEMES:
        print(f"  {k:12} {'(unmodified)' if THEMES[k] is None else THEMES[k]}")


def cmd_preview(upd, outdir='.'):
    from PIL import Image, ImageDraw
    _, secs = parse(upd)
    m, h, ln = _blk1(secs)
    dec = L.decode(bytes(m.img[h + 4:h + 4 + ln]))
    W0 = np.frombuffer(dec[:len(dec) // 2 * 2], dtype='<u2')
    def rgb(v):
        v = v.astype(np.uint32)
        return np.stack([((v >> 11) & 0x1F) * 255 // 31,
                         ((v >> 5) & 0x3F) * 255 // 63,
                         (v & 0x1F) * 255 // 31], -1).astype(np.uint8)
    cols = []
    for name in THEMES:
        new, cnt = recolour(W0, THEMES[name])
        print(f"  {name:12} {cnt:,} pixels")
        st = []
        for off, wd, rows in SPOTS:
            a = off // 2
            st.append(rgb(new[a:a + wd * rows]).reshape(rows, wd, 3))
            st.append(np.full((4, wd, 3), 25, np.uint8))
        mx = max(s.shape[1] for s in st)
        st = [np.concatenate([s, np.full((s.shape[0], mx - s.shape[1], 3), 25, np.uint8)], 1)
              for s in st]
        cols.append((name, np.concatenate(st, 0)))
    hh = max(c.shape[0] for _, c in cols)
    ww = sum(c.shape[1] for _, c in cols) + 12 * len(cols)
    sheet = Image.new('RGB', (ww, hh + 18), (18, 18, 18))
    dr = ImageDraw.Draw(sheet); x = 0
    for name, c in cols:
        sheet.paste(Image.fromarray(c), (x, 18))
        dr.text((x + 3, 4), name.upper(), fill=(230, 230, 230))
        x += c.shape[1] + 12
    p = os.path.join(outdir, 'themes_preview.png')
    sheet.resize((sheet.width * 2, sheet.height * 2), Image.NEAREST).save(p)
    print(f"wrote {p}")


def cmd_build(upd, theme, out=None):
    if theme not in THEMES:
        raise SystemExit(f"unknown theme {theme!r}; try: {', '.join(THEMES)}")
    out = out or f"build/XDJ700_{theme}.UPD"
    raw, secs = parse(upd)
    m, h, ln = _blk1(secs)
    dec = L.decode(bytes(m.img[h + 4:h + 4 + ln]))
    n = len(dec) // 2 * 2
    w = np.frombuffer(dec[:n], dtype='<u2').copy()
    new, cnt = recolour(w, THEMES[theme])
    buf = bytearray(dec); buf[:n] = new.tobytes()
    if bytes(buf) == dec:
        print(f"{theme}: no change; writing stock file")
        st = bytes(m.img[h + 4:h + 4 + ln])
    else:
        st = L.encode(bytes(buf))
        assert L.decode(st) == bytes(buf), 'encoder round-trip failed'
        m.write_block(h, ln, st)
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    open(out, 'wb').write(assemble(secs))
    # verify what we just wrote
    _, chk = parse(out)
    ok = all(o for _, _, _, _, o in chk[0].check_blocks())
    m2, h2, l2 = _blk1(chk)
    back = L.decode(bytes(m2.img[h2 + 4:h2 + 4 + l2]))
    print(f"{theme}: {cnt:,} px, stream {ln:,} -> {len(st):,} ({len(st)-ln:+,}), "
          f"checksums {'OK' if ok else 'BAD'}, decodes to intended edit: {back == bytes(buf)}")
    print(f"  wrote {out} ({os.path.getsize(out):,} B)")
    from safety import check
    print()
    if not check(upd, out):
        os.replace(out, out + '.REJECTED')
        raise SystemExit(f"\nSAFETY GATE REFUSED THIS BUILD.\n"
                         f"Renamed to {out}.REJECTED so it cannot be flashed by mistake.")


if __name__ == '__main__':
    c = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if c == 'list': cmd_list()
    elif c == 'preview': cmd_preview(*sys.argv[2:])
    elif c == 'build': cmd_build(*sys.argv[2:])
    else: raise SystemExit(__doc__)
