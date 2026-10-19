#!/usr/bin/env python3
"""x7k_patch.py -- deck-side .x7k key-shift header parser (CORRECTED).

Reads the .x7k INDEX (header at file offset 0 -- there is NO trailer;
x7k files start with magic "X7K1") when a new file handle appears on the
fread path, and publishes a compact table for later use (MY SETTINGS,
version selection). It does NOT replace vredirect: the dispatcher
tail-jumps to VCAVE (0x08D5FFA8's vredirect logic) after parsing.

Header layout (tools/x7k.py, ENTRY "<bBxxQQQ" = 28 B):
  magic 0..3 "X7K1" | ver 4..5 | rate 6..9 | ch 10..11 | frames 12..19
  bpm 20..23 | keycode 24 | nver 25 | sha 26..57 | entries @58 stride 28
  entry: semis +0 (i8) | codec +1 (u8) | off +4 (u64, low32 used)
         len +12 (u64, low32 used) | frames +20 (u64)

X7K area (deck): 0x081E0000 tail past PEND (zero run -> 0x081E03EB).
DECK SURVEY REQUIRED before any install (tools/deck_zero_survey.py).
NOT installed on deck (no sidecar audio path yet). Emu/box-tested only.

XTBL (own table, does NOT touch vredirect's VTBL):
  +0 nver u8 | +1.. 9 x {semis i8, codec u8, pad, off u32, len u32}
  (12 B each, longs 4-aligned)

Usage:
  python tools/x7k_patch.py plan    # sizes + hook bytes (deck, NEVER RUN)
  python tools/x7k_patch.py test    # sh4box parser test (offline, runs now)
"""
import os
import struct
import sys

sys.path.insert(0, "tools")
from shasm import assemble

XBASE = int(os.environ.get("X7K_BASE", "0x081E0000"), 0)
XEND = XBASE + 0x400
FREAD = int(os.environ.get("X7K_FREAD", "0x08C61A6C"), 0)
FSEEK3 = int(os.environ.get("X7K_FSEEK3", "0x08C62E1C"), 0)
VCAVE = int(os.environ.get("X7K_VCAVE", "0x081DB300"), 0)
HOOK = 0x08D5FFA8
MAGICV = 0x314B3758          # "X7K1" little-endian
MAXN = 9

# header 28 + sha 32 = 60; entries start 58? No:
# HDR 28 B (0..27): magic0-3 ver4-5 rate6-9 ch10-11 frames12-19 bpm20-23
# keycode24 nver25 bpm? recount: "<4sHIIQfBB": 4+2+4+2+8+4+1+1 = 26.
# nver @25, sha 26..57, entries @58 stride 28. 58 + 9*28 = 310.
READN = 60 + MAXN * 28       # 312

XFP = XBASE + 0x100          # u32 last-seen file handle (0 = none)
XTBL = XBASE + 0x104         # u8 nver + 3 pad + 9 x 12 B (ends +0x174)
XSCR = XBASE + 0x180          # 320-B header scratch (ends +0x2C0)


def src():
    return f"""
xdsp:
  sts.l pr,@-r15
  mov.l r14,@-r15
  mov.l r13,@-r15
  mov.l r12,@-r15
  mov.l r11,@-r15
  mov.l r10,@-r15
  mov.l r9,@-r15
  mov.l r8,@-r15
  mov r4,r8
  mov r5,r9
  mov r6,r10
  mov r7,r11
  mov.l @({XFP:#x},pc),r0
  mov.l @r0,r1
  cmp/eq r11,r1
  bf xparse
xhave:
  mov r8,r4
  mov r9,r5
  mov r10,r6
  mov r11,r7
  mov.l @r15+,r8
  mov.l @r15+,r9
  mov.l @r15+,r10
  mov.l @r15+,r11
  mov.l @r15+,r12
  mov.l @r15+,r13
  mov.l @r15+,r14
  lds.l @r15+,pr
  mov.l @({VCAVE:#x},pc),r0
  jmp @r0
  nop
xparse:
  mov.l r11,@r0
  mov r11,r4
  mov #0,r5
  mov #0,r6
  mov.l @({FSEEK3:#x},pc),r0
  jsr @r0
  nop
  tst r0,r0
  bf xfail
  mov.l @({XSCR:#x},pc),r4
  mov #1,r5
  mov #104,r6
  shll r6
  add #104,r6
  mov r11,r7
  mov.l @({FREAD:#x},pc),r0
  jsr @r0
  nop
  mov #104,r1
  shll r1
  add #104,r1
  cmp/eq r1,r0
  bf xfail
  mov.l @({XSCR:#x},pc),r1
  mov.l @r1,r0
  mov.l @({MAGICV:#x},pc),r2
  cmp/eq r2,r0
  bf xfail
  add #27,r1
  mov.b @r1,r0
  extu.b r0,r0
  mov #9,r2
  cmp/hi r2,r0
  bt xfail
  mov.l @({XTBL:#x},pc),r2
  mov.b r0,@r2
  mov r0,r12
  add #33,r1                  ; r1 was XSCR+27 (nver) -> XSCR+60 entries
  add #4,r2
  mov #0,r3
xloop:
  mov.b @r1,r0
  mov.b r0,@r2
  mov.b @(1,r1),r0
  mov.b r0,@(1,r2)
  mov.l @(8,r1),r0
  tst r0,r0
  bf xfail
  mov.l @(4,r1),r0
  mov.l r0,@(4,r2)
  mov.l @(16,r1),r0
  tst r0,r0
  bf xfail
  mov.l @(12,r1),r0
  mov.l r0,@(8,r2)
  add #28,r1
  add #12,r2
  add #1,r3
  cmp/eq r3,r12
  bf xloop
  nop
  bra xhave
  nop
xfail:
  mov.l @({XTBL:#x},pc),r0
  mov #0,r1
  mov.b r1,@r0
  bra xhave
  nop
  .pool
"""


def code():
    c = assemble(src())
    assert XBASE + len(c) <= XFP, f"code {len(c)} overruns data"
    assert XSCR + 320 <= XEND, "scratch overruns area"
    return c


def plan():
    c = code()
    out = [(XBASE + i, b) for i, b in enumerate(c)]
    out += [(HOOK + i, b) for i, b in
            enumerate(struct.pack("<I", XBASE))]
    print(f"code {len(c)} B @ {XBASE:#x}..{XBASE + len(c):#x}")
    print(f"XFP {XFP:#x} XTBL {XTBL:#x} XSCR {XSCR:#x} (end {XEND:#x})")
    print(f"hook pool {HOOK:#x} -> {XBASE:#x} (LAST step on deck)")
    print("DECK SURVEY REQUIRED first: "
          f"deck_zero_survey {XBASE:x}:{(XEND - XBASE):x} must read ALL ZERO")
    print("NOT FOR DECK USE (no sidecar audio path; parser only).")
    return out


def test():
    """sh4box parser test with stub fread/fseek + a real .x7k header."""
    import tempfile
    sys.path.insert(0, ".")
    import sh4box
    from emu.sh4 import PcHalt
    import x7k as X7
    import x7k_fw as XF

    # fixture: real header bytes (synth tone, 9 versions)
    td = tempfile.mkdtemp()
    tone = os.path.join(td, "t.wav")
    import numpy as np
    r = 44100
    t = np.arange(r) / r
    sig = np.stack([0.5 * np.sin(2 * np.pi * 440 * t)] * 2, 1).astype(
        np.float32)
    subprocess_run = __import__("subprocess").run
    subprocess_run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar",
                    str(r), "-ac", "2", "-i", "-", "-c:a", "pcm_s16le",
                    tone], input=sig.tobytes(), check=True,
                   capture_output=True)
    blob = X7.build(tone, list(range(-4, 5)), codec="mp3", bitrate=64)
    head = blob[:512]
    want = XF.parse_x7k_header(head)
    assert want and len(want["versions"]) == 9, "fixture bad"

    B = 0x0B200000                 # test area base (box RAM)
    os.environ["X7K_BASE"] = hex(B)
    STUB_SEEK = B + 0x800          # fseek stub: r4=fp,r5=off,r6=whence
    STUB_READ = B + 0x820          # fread stub: r4=buf,r5=size,r6=cnt,r7=fp
    STUB_RET = B + 0x840           # rts;nop (dispatcher tail target)
    FAKEFILE = B + 0x1000
    FH = 0x1234
    # reassemble with stubs (FREAD/FSEEK3/VCAVE overridden)
    os.environ["X7K_FREAD"] = hex(STUB_READ)
    os.environ["X7K_FSEEK3"] = hex(STUB_SEEK)
    os.environ["X7K_VCAVE"] = hex(STUB_RET)
    import importlib
    import x7k_patch as XP
    importlib.reload(XP)
    c = XP.code()
    stubs = (
        "mov #0,r0\nrts\nnop\n",       # seek: always ok
        "mov r6,r0\nrts\nnop\n",        # read: patched below (real copy)
    )
    from shasm import assemble as _a
    seek_c = _a(stubs[0])
    # fread stub: copy FAKEFILE[pos:pos+r5*r6] -> r4, return r6; pos tracked
    # in a box word. (pos always 0 here: parser seeks 0 then reads.)
    read_src = f"""
      mov.l @({B + 0x900:#x},pc),r1
      mov.l @r1,r1
      mov r4,r2
      mov r5,r3
      mul.l r3,r6
      sts macl,r3
      mov r3,r0
      mov r6,r3
    cploop:
      tst r3,r3
      bt cpdone
      mov.b @r1+,r0
      mov.b r0,@r2
      add #1,r2
      dt r3
      bf cploop
    cpdone:
      mov r6,r0
      rts
      nop
      .pool
    """
    read_c = _a(read_src)
    ret_c = _a("rts\nnop\n")
    data = {B + 0x900: struct.pack("<I", FAKEFILE), FAKEFILE: head,
            STUB_SEEK: seek_c, STUB_READ: read_c, STUB_RET: ret_c,
            XP.XTBL: b"\xaa" * 112, XP.XFP: b"\x00" * 4}
    cpu, mem, res = sh4box.run_code(
        c, regs={4: 0x0B300000, 5: 1, 6: 128, 7: FH}, data=data)
    print("run:", res)
    got_n = mem.r8(XP.XTBL)
    print(f"XTBL nver: {got_n} (want 9)")
    ok = got_n == 9
    for k, e in enumerate(want["versions"]):
        a = XP.XTBL + 4 + 12 * k
        s = mem.r8(a)
        if s & 0x80:
            s -= 256
        co = mem.r8(a + 1)
        off = mem.r32(a + 4)
        ln = mem.r32(a + 8)
        match = (s == e["semis"] and co == {"mp3": 1, "flac": 2, "wav": 3,
                                            "opus": 4, "aac": 5}[e["codec"]]
                 and off == (e["off"] & 0xFFFFFFFF)
                 and ln == (e["len"] & 0xFFFFFFFF))
        ok &= match
        if not match:
            print(f"  entry {k} MISMATCH: got {(s, co, off, ln)} "
                  f"want {(e['semis'], e['codec'], e['off'], e['len'])}")
    # regs restored? r4-r7 must equal inputs (dispatcher contract)
    regs_ok = (cpu.gr(4) == 0x0B300000 and cpu.gr(5) == 1
               and cpu.gr(6) == 128 and cpu.gr(7) == FH)
    print("regs restored:", regs_ok)
    ok &= regs_ok
    # stack balanced? rhi[7] (r15) back at top-256
    print("stack balanced:", cpu.rhi[7] == 0x0B000000 + 64 * 1024 * 1024 - 256)
    ok &= cpu.rhi[7] == 0x0B000000 + 64 * 1024 * 1024 - 256
    # negative control: same handle again -> no reparse (preset XFP +
    # poisoned table in THIS run's memory; table must survive intact).
    # (A fresh run_code starts zeroed memory, so preset both explicitly.)
    import struct as _st
    data2 = dict(data)
    data2[XP.XFP] = _st.pack("<I", FH)
    data2[XP.XTBL] = b"\xaa" * 112
    cpu2, mem2, res2 = sh4box.run_code(
        c, regs={4: 0x0B300000, 5: 1, 6: 128, 7: FH}, data=data2)
    print("reparse skipped:", mem2.r8(XP.XTBL) == 0xAA)
    ok &= mem2.r8(XP.XTBL) == 0xAA
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        sys.exit(test())
    plan()
