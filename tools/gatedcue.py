#!/usr/bin/env python3
"""gatedcue.py -- GATED hot cue for the XDJ-700 (RAM only, reboot = stock).

Definition
----------
Stock hot cue latches: press A/B/C = jump to the cue + keep playing after
you let go.  Gated = the sound plays ONLY while the pad is held; release
returns to the previous transport state:

  * was PAUSED (paused anywhere, paused at cue, CUE idle): press = stock
    cue + play (jump, start sounding); hold = keep sounding; release =
    pause at the cue (PlayPause asserted through the STOCK panel path
    until the firmware's own play word leaves 5).  Net: preview-while-held.
  * was PLAYING: press = stock cue + play (jump, keep playing); hold =
    keep playing; release = keep playing (no pause).  Net: stock latch,
    i.e. "resumes prior play" from the hot cue.  Gating a playing deck
    into silence on every tap would be hostile; the useful gate is the
    paused-start preview.  A one-branch change (see RELEASE LOGIC below)
    makes it full-momentary (always pause) if that is ever wanted.
  * was CUE-preview (holding CUE): treated as playing (prior==1).  Edge:
    do not press a hot cue while holding CUE; the result ends playing
    from the hot cue (safe; press CUE/PLAY to recover).
  * empty hot cue (no cue point stored): stock does nothing, mode never
    reaches 5, so release correctly does nothing (safe no-op).
  * OFF (default), non-PERFORM, MODS OFF, not-a-new-press, or press
    outside A/B/C: pure stock (hook tail-jumps untouched).

How a stock hot cue is dispatched (measured, not assumed)
---------------------------------------------------------
Hit-tester FUN_08DD0128(down, x, y) indexes the zone table 0x080D5D38
(entries 5 x u32 {x, y, w, h, handler}; A/B/C = zones 23/24/25 with
handlers 0x08DD0BA4/B4/C4).  Each handler sets ONE key bit and returns 1:

    A: byte 86 bit 0x40    (mov #86; or #64)
    B: byte 86 bit 0x80    (mov #86; or #128)
    C: byte 87 bit 0x01    (mov #87; or #1)

The key bitmap lives at 0x0B6C7A28+; PlayPause is byte 87 bit 0x04
(PAY+16 bit 0x01 in the panel payload, BUTTONS["PlayPause"]=(16,0x01)).
The downstream consumer turns the edge into jump + play (stock path).
We REUSE that path: on a gated press we save prior transport, arm the
latch, and tail-jump to stock so the firmware sets the bits itself.  On
release we never synthesise a jump -- we only (maybe) hold PlayPause.

Pause on release reuses the stock path too: while pause-pending and the
play word is still 5, the hook ORs bit 0x01 into PAY+16 every frame.  The
parser code AFTER the hit-tester call (0x8D52648+) propagates PAY+16..21
into the key bitmap this same frame, the snapshotter copies it, and the
stock PlayPause consumer pauses (mode 5 -> 3).  No busy-wait, no direct
key-bit poke (a synchronous poke can never be observed: single core),
no position save/restore (no position word + seek path exists in the
emu/DSP wall; deck listening must confirm the audible gate).

RELEASE LOGIC (prior-aware default; full-momentary = delete the GPRIOR
check in grelease so every armed release with mode==5 pends a pause).

Addresses
---------
  HOOK pool 0x08D5278C (sole user: parser 0x8D5263A) -> GCAVE.
  ORIG 0x08DD0128.  Args at entry: r4 = down (PAY+21 bit0), r5 = x_px,
  r6 = y_px (r4-r7 preserved for the tail-jump; scratch r0-r1 only).
  PERFORM screen word *0x0BA371D4 == 7.
  Zone state *0x0C0E75BC: 0x1E (30) idle = a NEW press.
  Play word *0x0B6C4264: 5 = playing, 3 = paused (toggles on PlayPause).
  PAY base 0x0B6C7A30; PAY+16 bit 0x01 = PlayPause (panel payload byte).
  A/B/C rect: x in [116,445), y in [232,296) (zone table 23-25).
  GSEL = mysettings.MSGATE (0x081DBDF8): 0 = OFF (stock, default),
  1 = GATE (the MY SETTINGS [HOT CUE] row; no new UI needed).
  MSOFF = mysettings.MSOFF: 1 = MODS OFF -> stock.
  GC0 = 0x081E0000: GLATCH +0, GPRIOR +1, GPEND +2, GCOUNT +3.  The
  0x081D8900 zero run ends at 0x081E03EB, so GC0..+0x3EB (1003 B) is the
  free tail past perform_pads PEND (0x081E0000).  Zero in every emu
  lineage; DECK-SURVEY REQUIRED before any hardware write (read-only BR,
  like rgb_overview_apply --check; never assume).

  gatedcue.py plan   byte counts / addresses
  gatedcue.py emu    emulator proof (needs cache/emu_perform_track.pkl)
"""
import sys

sys.path[:0] = [".", "tools"]
from shasm import assemble
import mysettings as MSET

GC0 = 0x081E0000
GLATCH = GC0 + 0
GPRIOR = GC0 + 1
GPEND = GC0 + 2
GCOUNT = GC0 + 3
GEND = GC0 + 0x400

HOOK_TOUCH, ORIG_TOUCH = 0x08D5278C, 0x08DD0128
PERFORMW = 0x0BA371D4
ZSTATE = 0x0C0E75BC
PLAYW = 0x0B6C4264
PAY16 = 0x0B6C7A30 + 16
GSEL = MSET.MSGATE
MSOFF = MSET.MSOFF

PEND_MAX = 90  # frames (~1.5 s at 60 fps) before a stuck pause gives up

SRC = f"""
ghook:
  mov.l @({GPEND:#x},pc),r1
  mov.b @r1,r0
  tst r0,r0
  bt gpress
  nop
  mov.l @({PLAYW:#x},pc),r1
  mov.l @r1,r0
  cmp/eq #5,r0
  bf gpend_done
  nop
  mov.l @({PAY16:#x},pc),r1
  mov.b @r1,r0
  or #1,r0
  mov.b r0,@r1
  mov.l @({GCOUNT:#x},pc),r1
  mov.b @r1,r0
  extu.b r0,r0
  add #1,r0
  mov.b r0,@r1
  cmp/eq #{PEND_MAX},r0
  bf gpress
  nop
  mov.l @({GPEND:#x},pc),r1
  mov #0,r0
  mov.b r0,@r1
  mov.l @({GCOUNT:#x},pc),r1
  mov.b r0,@r1
  bra gpress
  nop
gpend_done:
  mov.l @({GPEND:#x},pc),r1
  mov #0,r0
  mov.b r0,@r1
  mov.l @({GCOUNT:#x},pc),r1
  mov.b r0,@r1
gpress:
  tst r4,r4
  bt grelease
  nop
  mov.l @({GLATCH:#x},pc),r1
  mov.b @r1,r0
  tst r0,r0
  bf gstock
  nop
  mov.l @({PERFORMW:#x},pc),r1
  mov.l @r1,r0
  cmp/eq #7,r0
  bf gstock
  nop
  mov.l @({MSOFF:#x},pc),r1
  mov.b @r1,r0
  tst r0,r0
  bf gstock
  nop
  mov.l @({GSEL:#x},pc),r1
  mov.b @r1,r0
  tst r0,r0
  bt gstock
  nop
  mov.l @({ZSTATE:#x},pc),r1
  mov.l @r1,r0
  cmp/eq #30,r0
  bf gstock
  nop
  mov r5,r0
  add #-116,r0
  mov.l @(329,pc),r1
  cmp/hs r1,r0
  bt gstock
  nop
  mov r6,r0
  add #-100,r0
  add #-100,r0
  add #-32,r0
  mov #64,r1
  cmp/hs r1,r0
  bt gstock
  nop
  mov.l @({PLAYW:#x},pc),r1
  mov.l @r1,r0
  cmp/eq #5,r0
  movt r0
  mov.l @({GPRIOR:#x},pc),r1
  mov.b r0,@r1
  mov.l @({GPEND:#x},pc),r1
  mov #0,r0
  mov.b r0,@r1
  mov.l @({GCOUNT:#x},pc),r1
  mov.b r0,@r1
  mov.l @({GLATCH:#x},pc),r1
  mov #1,r0
  mov.b r0,@r1
  bra gstock
  nop
grelease:
  mov.l @({GLATCH:#x},pc),r1
  mov.b @r1,r0
  tst r0,r0
  bt gstock
  nop
  mov #0,r0
  mov.b r0,@r1
  mov.l @({GPRIOR:#x},pc),r1
  mov.b @r1,r0
  tst r0,r0
  bf gstock
  nop
  mov.l @({PLAYW:#x},pc),r1
  mov.l @r1,r0
  cmp/eq #5,r0
  bf gstock
  nop
  mov.l @({GPEND:#x},pc),r1
  mov #1,r0
  mov.b r0,@r1
  mov.l @({GCOUNT:#x},pc),r1
  mov #0,r0
  mov.b r0,@r1
gstock:
  mov.l @({ORIG_TOUCH:#x},pc),r0
  jmp @r0
  nop
"""


def code():
    c = assemble(SRC)
    assert GC0 + len(c) <= GEND, len(c)
    return c


def plan():
    c = code()
    print(f"cave {len(c)} B @ {GC0:#x}..{GC0 + len(c):#x} (free tail to {GEND:#x})")
    print(f"state GLATCH {GLATCH:#x} GPRIOR {GPRIOR:#x} "
          f"GPEND {GPEND:#x} GCOUNT {GCOUNT:#x}")
    print(f"GSEL (MY SETTINGS [HOT CUE]) {GSEL:#x} default 0=OFF")
    print(f"hook {HOOK_TOUCH:#x}: {ORIG_TOUCH:#x} -> {GC0:#x}")
    from shasm import Asm
    a = Asm()
    a.assemble(SRC)
    print("labels:", {k: f"{GC0 + v:#x}" for k, v in a.labels.items()})


def install(mem):
    c = code()
    for i, b in enumerate(c):
        mem.w8(GC0 + i, b)
    for a in (GLATCH, GPRIOR, GPEND, GCOUNT):
        mem.w8(a, 0)
    assert mem.r32(HOOK_TOUCH) == ORIG_TOUCH, hex(mem.r32(HOOK_TOUCH))
    mem.w32(HOOK_TOUCH, GC0)


def uninstall(mem):
    if mem.r32(HOOK_TOUCH) == GC0:
        mem.w32(HOOK_TOUCH, ORIG_TOUCH)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if cmd == "plan":
        plan()
    elif cmd == "emu":
        sys.path.insert(0, "tools")
        import gatedcue_emu as E
        sys.exit(0 if E.run() else 1)
    else:
        sys.exit(__doc__)
