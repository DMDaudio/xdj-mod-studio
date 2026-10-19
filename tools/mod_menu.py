#!/usr/bin/env python3
"""mod_menu.py -- an on-deck MOD MENU for the XDJ-700 (RAM only).

RELOOP/EXIT (re-pointed to key byte 93 bit 0x01 by tools/selector.py)
opens / closes a menu drawn over the centre waveform:

    XDJ-700 MODS
  > WAVEFORM    RGB          BLUE / RGB / 3-BAND   (overview + centre)
    VERSION     KEY+1        MIX / the loaded file's versions
    TURN KNOB = SELECT   PUSH = CHANGE   RELOOP = CLOSE

The browse knob moves the cursor (rotary counter u16 0x0B6C7B48), a knob
push (key byte 102 bit 0x20) changes the selected setting. On the main
screen neither does anything in stock firmware (emu: 0 px change).

Hook: the centre-waveform painter FUN_08DC39B6 draws into a staging
buffer, locks the on-screen layer (fn 0x08C2C984-family: frame @(0,r15)
= stride px, @(4,r15) = surface), copies, then RELEASES it through pool
0x08DC418C (users 0x8DC4084/0x8DC411A, both in this painter). The pool
points at MCAVE: it handles input and, while the menu is open, draws
the panel onto the locked surface (504 x 142 px RGB565, top-down), then
tail-jumps to the stock release, which shows it. The painter runs every frame while a track is loaded
(also paused), and not at all without a track -> install with no track
loaded (same gate as rgb_detail_apply.py), hook pool LAST.

Layout (all inside the deck-surveyed cave run, CAVE+0x2D00..+0x3800):
  MCAVE  code (input + actions)       MDRAW code (fill, text, menu)
  MDATA  strings, pointer tables      FONT  Helvetica Neue Bold 13 px
  ST     state: open, cursor, prev RELOOP, prev push, last rotary, tmp

  mod_menu.py plan      byte count / addresses
  mod_menu.py emu       emulator test (open, navigate, change all three,
                        screenshots; C core)
"""
import os
import struct
import sys

sys.path[:0] = [".", "tools"]
from shasm import assemble
import rgb_overview as RO
import vredirect as VR

HOOK = 0x08DC418C                 # pool: centre painter's layer release
RELEASE = 0x08C2C984              # its stock value (same fn as overview's)
HOOK_GATE = 0x08DC4A50            # pool: "skip the centre painter?" check
GATE = 0x08DB3F4A                 # its stock value (r0 != 0 -> skip)
ROWS = 142
COLS = 504
KEY93 = 0x0B6C7A28 + 93           # RELOOP (re-pointed), bit 0x01
KEY102 = 0x0B6C7A28 + 102         # rotary push, bit 0x20
ROT = 0x0B6C7B48                  # rotary counter (u16)

MCAVE = RO.CAVE + 0x2D00          # 0x081DB600
MDRAW = MCAVE + 0x200
MDATA = MCAVE + 0x500
FONT = MCAVE + 0x600
ST = MCAVE + 0xBE0                # state: +0 open +1 cursor +2 prev RELOOP
SURF = ST + 8                     # +3 prev push +4 last rotary (u16)
SSTRIDE = ST + 12                 # surface pointer / stride in BYTES
TMP = ST + 16                     # 9-B name buffer
MEND = MCAVE + 0xC00

FONT_FILE = "/System/Library/Fonts/HelveticaNeue.ttc"
CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-:.>=()/[]"
GH = 13                           # glyph rows

# stock MY SETTINGS popup look (XDJ-1000MK2 / 700 utility lists): slate
# panel, light header with dark title, white bar + dark text on the
# selected row, light-grey text elsewhere
BG, HEAD, TITLE, SELBAR, SELTXT, TXT, DIM = \
    0x3A4A, 0xCE59, 0x2945, 0xF79E, 0x2104, 0xBDF7, 0x8C71


def build_font():
    """MAP[64] (ASCII 32..95 -> glyph or 0xFF), WID[n], GLY[n][13] u16
    rows (bit 15 = leftmost pixel)."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(FONT_FILE, 16, index=1)
    mp = bytearray([0xFF] * 64)
    wid, gly = bytearray(), bytearray()
    for gi, ch in enumerate(CHARS):
        mp[ord(ch) - 32] = gi
        im = Image.new("L", (16, GH), 0)
        ImageDraw.Draw(im).text((0, 12), ch, font=f, fill=255, anchor="ls")
        px = im.load()
        wid.append(min(16, max(4, round(f.getlength(ch)))))
        for y in range(GH):
            bits = 0
            for x in range(16):
                if px[x, y] >= 110:
                    bits |= 0x8000 >> x
            gly += struct.pack("<H", bits)
    return bytes(mp), bytes(wid), bytes(gly)


def font_blob():
    mp, wid, gly = build_font()
    return mp + wid.ljust(64, b"\0") + gly


F_MAP, F_WID, F_GLY = FONT, FONT + 64, FONT + 128

STRINGS = {
    "title": "MY SETTINGS",
    "l0": "[WAVE FORM COLOR]", "l1": "[KEY / STEMS]",
    "w0": "BLUE", "w1": "RGB", "w2": "3-BAND",
    "mix": "MIX", "none": "NONE",
    "cur": ">",
    "hint": "TURN: SELECT    PUSH: CHANGE    RELOOP: CLOSE",
}


def data_layout():
    """(blob, {name: absolute address}) for MDATA: strings + tables."""
    blob, addr = bytearray(), {}
    for k, s in STRINGS.items():
        addr[k] = MDATA + len(blob)
        blob += s.encode() + b"\0"
    while len(blob) % 4:
        blob += b"\0"
    addr["labels"] = MDATA + len(blob)
    for k in ("l0", "l1"):
        blob += struct.pack("<I", addr[k])
    addr["waves"] = MDATA + len(blob)
    for k in ("w0", "w1", "w2"):
        blob += struct.pack("<I", addr[k])
    assert MDATA + len(blob) <= FONT, len(blob)
    return bytes(blob), addr


_, A = data_layout()

MWRAP_SRC = f"""
mrel:                       ; = the painter's layer release: r4 = handle,
  mov.l @r15,r1             ; painter frame @(0,r15) stride px,
  mov.l @(4,r15),r2         ; @(4,r15) locked surface (0 = lock failed)
  sts.l pr,@-r15
  mov.l r4,@-r15
  mov.l @({ST:#x},pc),r3
  mov.l r2,@(8,r3)          ; SURF
  add r1,r1
  mov.l r1,@(12,r3)         ; SSTRIDE (bytes)
  mov.b @(0,r3),r0
  tst r0,r0                 ; menu open and a surface -> draw the panel
  bt rdone
  tst r2,r2
  bt rdone
  mov.l @({MDRAW:#x},pc),r0
  jsr @r0
  nop
rdone:
  mov.l @r15+,r4
  lds.l @r15+,pr
  mov.l @({RELEASE:#x},pc),r0
  jmp @r0                   ; stock release (shows the layer)
  nop
mgate:                      ; every frame with a track loaded: input, and
  sts.l pr,@-r15            ; while the menu is open force the painter on
  mov.l @({GATE:#x},pc),r0  ; (tracks without waveform data skip it and
  jsr @r0                   ; drop the layer -> nowhere to draw)
  nop
  mov.l r0,@-r15
  bsr minput
  nop
  mov.l @r15+,r0
  mov.l @({ST:#x},pc),r1
  mov.b @r1,r2
  tst r2,r2
  bt gdone
  mov #0,r0
gdone:
  lds.l @r15+,pr
  rts
  nop
minput:
  sts.l pr,@-r15
  mov.l r8,@-r15
  mov.l r9,@-r15
  mov.l r10,@-r15
  mov.l r11,@-r15
  mov.l @({ST:#x},pc),r8
  mov.l @({KEY93:#x},pc),r1 ; RELOOP press edge -> open / close
  mov.b @r1,r0
  and #1,r0
  mov r0,r2
  mov.b @(2,r8),r0
  mov r0,r3
  mov r2,r0
  mov.b r0,@(2,r8)
  cmp/eq r2,r3
  bt nore
  tst r2,r2
  bt nore
  mov.b @(0,r8),r0
  xor #1,r0
  mov.b r0,@(0,r8)
nore:
  mov.l @({KEY102:#x},pc),r1 ; knob push edge -> r9
  mov.b @r1,r0
  and #32,r0
  mov r0,r2
  mov.b @(3,r8),r0
  mov r0,r3
  mov r2,r0
  mov.b r0,@(3,r8)
  mov #0,r9
  cmp/eq r2,r3
  bt nopu
  tst r2,r2
  bt nopu
  mov #1,r9
nopu:
  mov.l @({ROT:#x},pc),r1   ; knob turn since last frame -> r10
  mov.w @r1,r2
  mov.w @(4,r8),r0
  mov r0,r3
  mov r2,r0
  mov.w r0,@(4,r8)
  sub r3,r2
  exts.w r2,r10
  mov.b @(0,r8),r0
  tst r0,r0
  bt mout                   ; menu closed: nothing else  mov.b @(1,r8),r0
  extu.b r0,r11             ; cursor
  cmp/pl r10
  bf nodn
  mov #8,r1
  cmp/gt r1,r10            ; big jumps = counter resync, ignore
  bt nodn
  add #1,r11
nodn:
  cmp/pz r10
  bt noup
  mov #-8,r1
  cmp/ge r1,r10
  bf noup
  add #2,r11
noup:
  mov #2,r1
  cmp/hs r1,r11
  bf c1
  add #-2,r11
c1:
  mov r11,r0
  mov.b r0,@(1,r8)
  tst r9,r9
  bt nact
  mov r11,r0
  cmp/eq #0,r0
  bt a0
  cmp/eq #1,r0
  bt a1
  bra nact
  nop
a0:
  mov.l @({RO.MODE_ADDR:#x},pc),r1   ; WAVEFORM: blue -> rgb -> 3band
  mov.b @r1,r0
  add #1,r0
  cmp/eq #3,r0
  bf a0s
  mov #0,r0
a0s:
  bra nact
  mov.b r0,@r1
a1:
  mov.l @({VR.VTBL + 8:#x},pc),r1   ; VERSION: mix -> 1 .. n -> mix
  mov.l @r1,r2
  tst r2,r2
  bt nact
  mov.l @({VR.VSEL:#x},pc),r1
  mov.b @r1,r0
  extu.b r0,r0
  add #1,r0
  cmp/hi r2,r0
  bf a1s
  mov #0,r0
a1s:
  mov.b r0,@r1
nact:
mout:
  mov.l @r15+,r11
  mov.l @r15+,r10
  mov.l @r15+,r9
  mov.l @r15+,r8
  lds.l @r15+,pr
  rts
  nop
"""

MDRAW_SRC = f"""
drawmenu:
  sts.l pr,@-r15
  mov.l r8,@-r15
  mov.l r9,@-r15
  mov.l r10,@-r15
  mov.l r11,@-r15
  mov.l r12,@-r15
  mov.l r13,@-r15
  mov #0,r4                 ; panel background
  mov.l @({ROWS:#x},pc),r5
  mov.l @({BG:#x},pc),r6
  bsr fillrows
  nop
  mov #0,r4                 ; light header strip with the title
  mov #26,r5
  mov.l @({HEAD:#x},pc),r6
  bsr fillrows
  nop
  mov.l @({188:#x},pc),r4
  mov #6,r5
  mov.l @({A["title"]:#x},pc),r6
  mov.l @({TITLE:#x},pc),r7
  bsr drawstr
  nop
  mov #1,r12                ; item
items:
  mov r12,r0                ; y = 34 + 28 * i -> r9
  shll2 r0
  mov r0,r9
  shll2 r9
  shll r9
  sub r0,r9
  add #38,r9
  mov.l @({ST:#x},pc),r1
  mov.b @(1,r1),r0
  extu.b r0,r0
  mov.l @({TXT:#x},pc),r13
  cmp/eq r12,r0
  bf ncur
  mov.l @({SELTXT:#x},pc),r13
  mov r9,r4                 ; selected row: white bar
  add #-6,r4
  mov #26,r5
  mov.l @({SELBAR:#x},pc),r6
  bsr fillrows
  nop
ncur:
  mov.l @({A["labels"]:#x},pc),r1
  mov r12,r0
  shll2 r0
  mov.l @(r0,r1),r6
  mov #24,r4
  mov r9,r5
  bsr drawstr
  mov r13,r7
  mov r12,r0                ; value string -> r6
  cmp/eq #0,r0
  bt v0
  cmp/eq #1,r0
  bt v1
  bra vdraw
  nop
v0:
  mov.l @({RO.MODE_ADDR:#x},pc),r1
  mov.b @r1,r0
  extu.b r0,r0
  mov #3,r1
  cmp/hs r1,r0
  bf v0ok
  mov #0,r0
v0ok:
  shll2 r0
  mov.l @({A["waves"]:#x},pc),r1
  bra vdraw
  mov.l @(r0,r1),r6
v1:
  mov.l @({VR.VTBL + 8:#x},pc),r1
  mov.l @r1,r2
  mov.l @({A["none"]:#x},pc),r6
  tst r2,r2
  bt vdraw
  mov.l @({VR.VSEL:#x},pc),r1
  mov.b @r1,r0
  extu.b r0,r0
  mov.l @({A["mix"]:#x},pc),r6
  tst r0,r0
  bt vdraw
  cmp/hi r2,r0
  bt vdraw
  add #-1,r0                ; copy name k-1 (8 B, maybe unterminated)
  shll2 r0
  add r0,r0
  mov.l @({VR.VNAMES:#x},pc),r1
  add r0,r1
  mov.l @({TMP:#x},pc),r6
  mov.l @r1+,r0
  mov.l r0,@r6
  mov.l @r1,r0
  mov.l r0,@(4,r6)
  mov #0,r0
  mov.b r0,@(8,r6)
vdraw:
  mov.l @({300:#x},pc),r4
  mov r9,r5
  bsr drawstr
  mov r13,r7
  add #1,r12
  mov #2,r0
  cmp/hs r0,r12
  bt idone
  bra items
  nop
idone:
  mov #16,r4
  mov #125,r5
  mov.l @({A["hint"]:#x},pc),r6
  mov.l @({DIM:#x},pc),r7
  bsr drawstr
  nop
  mov.l @r15+,r13
  mov.l @r15+,r12
  mov.l @r15+,r11
  mov.l @r15+,r10
  mov.l @r15+,r9
  mov.l @r15+,r8
  lds.l @r15+,pr
  rts
  nop
fillrows:                   ; r4 first row, r5 rows, r6 colour
  mov.l @({SSTRIDE:#x},pc),r1
  mov.l @r1,r1
  mulu.w r4,r1
  sts macl,r4
  mov.l @({SURF:#x},pc),r0
  mov.l @r0,r0
  add r0,r4                 ; row pointer
fr1:
  mov r4,r2
  mov.l @({COLS:#x},pc),r3
fs1:
  mov.w r6,@r2
  dt r3
  bf/s fs1
  add #2,r2
  dt r5
  bf/s fr1
  add r1,r4
  rts
  nop
drawstr:                    ; r4 x, r5 y, r6 string, r7 colour
  mov.l r8,@-r15
  mov.l r9,@-r15
  mov.l r10,@-r15
  mov.l r11,@-r15
  mov.l r12,@-r15
  mov r4,r8                 ; x
  mov r6,r9                 ; string
  mov r7,r10                ; colour
  mov.l @({SSTRIDE:#x},pc),r0
  mov.l @r0,r0
  mulu.w r5,r0
  sts macl,r11
  mov.l @({SURF:#x},pc),r0
  mov.l @r0,r0
  add r0,r11                ; row y base
ch:
  mov.b @r9+,r0
  extu.b r0,r0
  tst r0,r0
  bt sdone
  mov #97,r1                ; a-z -> A-Z
  cmp/hs r1,r0
  bf up
  add #-32,r0
up:
  add #-32,r0
  mov #64,r1
  cmp/hs r1,r0
  bt sp
  mov.l @({F_MAP:#x},pc),r1
  mov.b @(r0,r1),r0
  extu.b r0,r0
  mov #127,r1
  cmp/hi r1,r0
  bt sp
  mov r0,r12                ; glyph index
  mov.l @({F_WID:#x},pc),r1
  mov.b @(r0,r1),r3
  extu.b r3,r3              ; advance width
  mov.l @({496:#x},pc),r1   ; clip: stop at the right edge
  mov r8,r0
  add r3,r0
  cmp/hi r1,r0
  bt sdone
  mov r12,r0                ; rows = GLY + gi * 26
  shll2 r0
  mov r0,r1
  shll2 r0
  shll r0                   ; gi * 32
  sub r1,r0                 ; gi * 28
  sub r12,r0
  sub r12,r0                ; gi * 26
  mov.l @({F_GLY:#x},pc),r1
  add r0,r1
  mov r8,r2
  shll r2
  add r11,r2                ; pixel pointer
  mov #{GH},r6
row:
  mov.w @r1+,r0
  shll16 r0
  mov r2,r4
  mov r3,r5
cl:
  shll r0
  bf nopx
  mov.w r10,@r4
nopx:
  dt r5
  bf/s cl
  add #2,r4
  mov.l @({SSTRIDE:#x},pc),r4
  mov.l @r4,r4
  add r4,r2
  dt r6
  bf row
  bra ch
  add r3,r8
sp:
  bra ch
  add #5,r8
sdone:
  mov.l @r15+,r12
  mov.l @r15+,r11
  mov.l @r15+,r10
  mov.l @r15+,r9
  mov.l @r15+,r8
  rts
  nop
"""


def entries():
    """Absolute addresses of the two hook entry points."""
    from shasm import Asm
    a = Asm()
    a.assemble(MWRAP_SRC)
    return MCAVE + a.labels["mrel"], MCAVE + a.labels["mgate"]


def hooks():
    """[(pool, stock value, our value)] -- written LAST, no track loaded."""
    rel, gate = entries()
    return [(HOOK, RELEASE, rel), (HOOK_GATE, GATE, gate)]


def blobs():
    """[(address, bytes)] in install order (hook pools NOT included)."""
    w, d = assemble(MWRAP_SRC), assemble(MDRAW_SRC)
    data, _ = data_layout()
    fb = font_blob()
    assert MCAVE + len(w) <= MDRAW, len(w)
    assert MDRAW + len(d) <= MDATA, len(d)
    assert FONT + len(fb) <= ST, len(fb)
    return [(MCAVE, w), (MDRAW, d), (MDATA, data), (FONT, fb)]


def image():
    """Whole MCAVE..MEND image (state zero) -- what the deck should hold."""
    img = bytearray(MEND - MCAVE)
    for a, b in blobs():
        img[a - MCAVE:a - MCAVE + len(b)] = b
    return bytes(img)


def install(mem):
    for a, b in blobs():
        for i, x in enumerate(b):
            mem.w8(a + i, x)
    for pool, old, new in hooks():
        assert mem.r32(pool) == old, hex(pool)
        mem.w32(pool, new)


def emu():
    """Everything the deck session installs, then drive the menu."""
    import numpy as np
    from PIL import Image
    from emu.machine import Machine
    from emu import panl
    from ghosttap_emu import fb_png
    import rgb_detail as RD
    import detail_fake as DF
    import selector as SEL
    ext = os.environ.get("EXT", os.path.expanduser(
        "~/Downloads/Rekordbox stuff/PIONEER/USBANLZ/P000/0002D83A/"
        "ANLZ0000.EXT"))
    m = Machine.load(os.environ.get("CKPT", "cache/emu_nav_track2.pkl"))
    mem, dev, c = m.mem, m.mem.dev, m.cpu
    for a, old, new in SEL.plan_bytes():                  # RELOOP re-point
        assert mem.r8(a) == old
        mem.w8(a, new)
    for a, b in RO.plan(RO.MODES["rgb"])[1]:              # overview+snapshot
        mem.w8(a, b)
    for i, b in enumerate(RD.copy_bytes()):
        mem.w8(RD.COPY5 + i, b)
    RD.install(mem)                                       # centre waveform
    for a, b in VR.plan():                                # versions
        mem.w8(a, b)
    install(mem)                                          # menu
    out = os.environ.get("OUT", "screenshots/menu")
    os.makedirs(out, exist_ok=True)

    def key(k, hold=12_000_000, after=14_000_000):
        dev.panel_frame = panl.build_frame(keys=[k])
        c.run(limit=hold)
        dev.panel_frame = panl.build_frame()
        c.run(limit=after)

    def turn(n):
        mem.w16(ROT, (mem.r16(ROT) + n) & 0xFFFF)
        c.run(limit=14_000_000)

    key("RotaryPush", after=40_000_000)                   # load the track
    key("Browse", after=30_000_000)                       # main screen
    DF.install(m, ext, int(os.environ.get("POS", "3000")))
    c.run(limit=10_000_000)
    ok = True
    fb_png(m, f"{out}/0_closed.png")
    key("ReloopExit")
    st = [mem.r8(ST + i) for i in range(2)]
    print("after RELOOP: open/cursor", st)
    ok &= st == [1, 0]
    fb_png(m, f"{out}/1_open.png")
    mode0 = mem.r8(RO.MODE_ADDR)
    key("RotaryPush")
    print(f"WAVEFORM push: mode {mode0} -> {mem.r8(RO.MODE_ADDR)}")
    ok &= mem.r8(RO.MODE_ADDR) == (mode0 + 1) % 3
    fb_png(m, f"{out}/2_wave_changed.png")
    turn(1)
    print("cursor after 1 knob step:", mem.r8(ST + 1))
    ok &= mem.r8(ST + 1) == 1
    mem.w32(VR.VTBL + 8, 2)                  # pretend a 2-version file
    for k, nm in enumerate((b"KEY-1", b"KEY+1")):
        for i, ch in enumerate(nm.ljust(8, b"\0")):
            mem.w8(VR.VNAMES + 8 * k + i, ch)
    seq = [mem.r8(VR.VSEL)]
    for _ in range(4):
        key("RotaryPush")
        seq.append(mem.r8(VR.VSEL))
    print("VERSION pushes -> VSEL", seq, "(want 0,1,2,0,1)")
    ok &= seq == [0, 1, 2, 0, 1]
    fb_png(m, f"{out}/4_version.png")
    key("ReloopExit")
    print("after RELOOP: open", mem.r8(ST))
    ok &= mem.r8(ST) == 0
    fb_png(m, f"{out}/5_closed_again.png")
    a = np.asarray(Image.open(f"{out}/1_open.png").convert("RGB")).astype(int)
    b = np.asarray(Image.open(f"{out}/5_closed_again.png").convert("RGB")).astype(int)
    panel = a[170:312, 110:614]
    print(f"menu panel pixels non-black: {int((panel.sum(2) > 0).sum())}; "
          f"closed-vs-open differ: {int((a != b).any(2).sum())}")
    ok &= int((a != b).any(2).sum()) > 20000
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if sys.argv[1:2] == ["plan"]:
        for a, b in blobs():
            print(f"{a:#x}: {len(b)} B")
        for pool, old, new in hooks():
            print(f"hook {pool:#x}: {old:#x} -> {new:#x}")
        print(f"image {MCAVE:#x}..{MEND:#x}")
        return 0
    return emu()


if __name__ == "__main__":
    sys.exit(main())
