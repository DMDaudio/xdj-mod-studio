#!/usr/bin/env python3
"""theme_deck.py -- apply a mods/themes.py theme to the RUNNING deck (RAM only).

A theme rewrites 2-3 MB of artwork -- impossible byte-by-byte over the
USB shell. But the recolour is a pure per-pixel colour function inside
fixed artwork regions (verified: applying the src->dst colour map inside
the 270 selected regions == themes.recolour, 0 px different). So the
deck gets:
  * a theme data blob (~7 KB): header + regions + sorted colour map,
  * a small routine (THEME_CODE) that walks the regions and replaces
    each pixel found in the map (binary search),
  * a one-shot flag byte. The waveform cave (tools/rgb_overview.py)
    calls THEME_CODE at its entry when the flag is set, then clears it
    -> the theme applies on the next track load (painter hook), ~1-2 s
    UI pause once. Apply while NOT playing. Reboot = stock artwork.

Blob layout @THEME_DATA (LE):
  +0 u32 nregions   +4 u32 src_map_addr   +8 u32 nmap
  +12 nregions x {u32 start_addr, u32 pixel_count}
  then nmap x u16 src (sorted ascending), then nmap x u16 dst.

  theme_deck.py emu NAME       (apply in emulator, verify vs recolour)
  theme_deck.py plan NAME      (sizes for deck delivery)
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
sys.path.insert(0, "mods")
from shasm import assemble
import rgb_overview as RO

THEME_CODE = RO.CAVE + 0x800
THEME_DATA = RO.CAVE + 0xA00
THEME_FLAG = RO.MODE_ADDR + 1          # u8: 1 = apply theme on next hook
IMG_BASE = 0x08000000                  # block1 unpack address


def stock_words():
    from emu import fwhome
    raw = open(fwhome.path("blk1.bin"), "rb").read()
    return np.frombuffer(raw[:len(raw) // 2 * 2], "<u2")


def theme_tables(name, w=None):
    import themes as T
    w = stock_words() if w is None else w
    art, nb = T.art_windows(w)
    heur = art & ~T.text_windows(w, nb)
    pic = np.repeat(heur, T.WIN) | T.proven_sprites(len(w))[:nb * T.WIN]
    pic = np.pad(pic, (0, max(0, len(w) - len(pic))))[:len(w)]
    new = T.recolour(w.copy(), T.THEMES[name])[0]
    ch = new != w
    src, idx = np.unique(w[ch], return_index=True)
    dst = new[ch][idx]
    d = np.diff(np.concatenate([[0], pic.astype(np.int8), [0]]))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    regions = []
    lo, hi = RO.CAVE, RO.CAVE + 0x4000          # never recolour the cave
    for s, e in zip(starts, ends):
        a, b = IMG_BASE + 2 * int(s), IMG_BASE + 2 * int(e)
        for x, y in ((a, min(b, lo)), (max(a, hi), b)):
            if y > x:
                regions.append((x, (y - x) // 2))
    return regions, src.astype("<u2"), dst.astype("<u2"), new


def blob(name):
    regions, src, dst, _ = theme_tables(name)
    hdr_len = 12 + 8 * len(regions)
    src_addr = THEME_DATA + hdr_len
    out = struct.pack("<III", len(regions), src_addr, len(src))
    for a, n in regions:
        out += struct.pack("<II", a, n)
    return out + src.tobytes() + dst.tobytes()


SRC = """
theme:
  mov.l @({DATA:#x},pc),r8
  mov.l @r8+,r9             ; nregions
  mov.l @r8+,r12            ; src map base
  mov.l @r8+,r13            ; nmap
  mov r13,r14
  add r14,r14
  add r12,r14               ; dst map base = src + 2*nmap
rloop:
  tst r9,r9
  bt tdone
  mov.l @r8+,r10            ; region start
  mov.l @r8+,r11            ; region pixels
ploop:
  tst r11,r11
  bt rnext
  mov.w @r10,r0
  extu.w r0,r0              ; v
  mov #0,r1                 ; lo
  mov r13,r2                ; hi
bs:
  cmp/hs r2,r1              ; lo >= hi -> miss
  bt miss
  mov r1,r3
  add r2,r3
  shlr r3                   ; mid
  mov r3,r4
  add r4,r4
  add r12,r4
  mov.w @r4,r5
  extu.w r5,r5
  cmp/eq r0,r5
  bt hit
  cmp/hi r5,r0              ; v > m ?
  bf golo
  mov r3,r1
  bra bs
  add #1,r1                 ; lo = mid+1
golo:
  bra bs
  mov r3,r2                 ; hi = mid
hit:
  mov r3,r4
  add r4,r4
  add r14,r4
  mov.w @r4,r5
  mov.w r5,@r10
miss:
  add #2,r10
  bra ploop
  add #-1,r11
rnext:
  bra rloop
  add #-1,r9
tdone:
  rts
  nop
"""


def theme_code():
    return assemble(SRC.format(DATA=THEME_DATA))


def emu(name):
    from emu.machine import Machine
    from emu import panl
    from ghosttap_emu import fb_png
    ckpt = os.environ.get("CKPT", "cache/emu_nav_track2.pkl")
    m = Machine.load(ckpt)
    mem = m.mem
    _, writes = RO.plan(RO.MODES[os.environ.get("MODE", "rgb")])
    for a, b in writes:
        mem.w8(a, b)
    code, data = theme_code(), blob(name)
    assert THEME_CODE + len(code) <= THEME_DATA
    for i, b in enumerate(code):
        mem.w8(THEME_CODE + i, b)
    for i, b in enumerate(data):
        mem.w8(THEME_DATA + i, b)
    mem.w8(THEME_FLAG, 1)
    w = stock_words()
    before = np.frombuffer(bytes(mem.ram0[:len(w) * 2]), "<u2").copy()
    mem.dev.panel_frame = panl.build_frame(keys=["RotaryPush"])
    m.cpu.run(limit=3_000_000)
    mem.dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=int(os.environ.get("STEPS", "120000000")))
    after = np.frombuffer(bytes(mem.ram0[:len(w) * 2]), "<u2")
    _, src, dst, new = theme_tables(name, w)
    # expected: wherever RAM still held the stock pixel, it now holds the
    # themed pixel; everything else unchanged by the theme pass
    regions, _, _, _ = theme_tables(name, w)
    sel = np.zeros(len(w), bool)
    for a, n in regions:
        s = (a - IMG_BASE) // 2
        sel[s:s + n] = True
    lut = np.arange(65536, dtype=np.uint16)
    lut[src] = dst
    exp = before.copy()
    exp[sel] = lut[before[sel]]
    changed_by_theme = int((after != before).sum())
    mis = (after != exp) & sel
    missed = mis & (after == before)            # theme should have hit
    foreign = mis & (after != before)           # firmware changed it too
    print(f"mismatch: theme-missed={int(missed.sum()):,} "
          f"changed-by-firmware={int(foreign.sum()):,}")
    if missed.any():
        i = np.nonzero(missed)[0][:5]
        print("  missed e.g.", [(hex(IMG_BASE + 2 * int(k)), hex(int(before[k])),
                                 hex(int(exp[k]))) for k in i])
    bad = int(missed.sum())
    print(f"flag after={mem.r8(THEME_FLAG)} pixels changed={changed_by_theme:,} "
          f"region mismatches={bad}")
    for k in ("Browse", "Taglist", "Browse", "Browse"):   # force redraws
        mem.dev.panel_frame = panl.build_frame(keys=[k])
        m.cpu.run(limit=3_000_000)
        mem.dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=20_000_000)
    fb_png(m, f"screenshots/theme_deck_{name}.png")
    ok = mem.r8(THEME_FLAG) == 0 and bad == 0 and changed_by_theme > 100000
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    mode, name = (sys.argv[1:3] + ["emu", "classic-blue"])[:2] if len(sys.argv) > 2 \
        else (sys.argv[1] if len(sys.argv) > 1 else "emu", "classic-blue")
    if mode == "plan":
        c, d = theme_code(), blob(name)
        print(f"theme {name}: code {len(c)} B @ {THEME_CODE:#x}, data {len(d)} B "
              f"@ {THEME_DATA:#x}, flag @ {THEME_FLAG:#x}")
        return 0
    return emu(name)


if __name__ == "__main__":
    sys.exit(main())
