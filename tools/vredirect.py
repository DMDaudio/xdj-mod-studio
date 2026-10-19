#!/usr/bin/env python3
"""vredirect.py -- play another VERSION of the loaded track (stems mixes /
key shifts from tools/versionfile.py) by redirecting the deck's audio reads.

Media read server FUN_08D5FBC8 reads USB audio with
    jsr [0x8D5FFA8] = fread 0x08C61A6C (r4 buf, r5 size, r6 count, r7 file)
(pool used only at 0x8D5FD32). Hook = that pool -> VCAVE:
  VSEL == 0                       -> plain fread (stock)
  new file handle                 -> read its 128-B TRAILER once (fseek -128
                                     from end, fread, fseek back) -> table
  pos in [data_off, data_off+len) -> fseek(pos + rel[VSEL-1]); fread;
                                     fseek(pos + read) -> the deck never
                                     sees the jump; perfect sync
  else                            -> plain fread
fseek = the deck's 3-arg wrapper FUN_08C62E1C(file, off, whence); file
position lives at handle +0x2C (emu-measured, +0x24C0 per read).

  vredirect.py plan
  vredirect.py emu        (build a version file, mount, load it in the
                           emulator with VSEL set, check the audio buffers
                           hold the chosen version's bytes)
"""
import os
import struct
import subprocess
import sys

sys.path[:0] = [".", "tools"]
from shasm import assemble
import rgb_overview as RO

HOOK = 0x08D5FFA8
FREAD = 0x08C61A6C
FSEEK3 = 0x08C62E1C
VCAVE = RO.CAVE + 0x2A00              # 0x081DB300 (survey before deck use)
VDATA = VCAVE + 0x200
VSEL = VDATA + 0x00                   # u8 selected version (0 = mix)
VFP = VDATA + 0x04                    # u32 handle the table belongs to
VTBL = VDATA + 0x08                   # data_off, data_len, n, rel[8]
VSCR = VDATA + 0x40                   # 128-B trailer scratch (names @+48)
VNAMES = VSCR + 48                    # 8 x 8 B version names (menu)
VEND = VSCR + 0x80
MAGIC = struct.unpack("<I", b"XVTR")[0]

SRC = f"""
vc:
  mov.l @({VFP:#x},pc),r0
  mov.l @r0,r0
  cmp/eq r7,r0              ; same file as last read?
  bf vgo                    ; no: (re)load its version table
  mov.l @({VSEL:#x},pc),r0
  mov.b @r0,r0
  extu.b r0,r0
  tst r0,r0
  bf vgo
  mov.l @({FREAD:#x},pc),r0
  jmp @r0                   ; version 0: stock read, untouched
  nop
vgo:
  sts.l pr,@-r15
  mov.l r8,@-r15
  mov.l r9,@-r15
  mov.l r10,@-r15
  mov.l r11,@-r15
  mov.l r12,@-r15
  mov.l r13,@-r15
  mov r4,r8
  mov r5,r9
  mov r6,r10
  mov r7,r11
  mov.l @({VFP:#x},pc),r1
  mov.l @r1,r0
  cmp/eq r11,r0
  bt vhave
  mov.l r11,@r1             ; new file: remember it, table invalid,
  mov.l @({VTBL:#x},pc),r1  ; and start on the plain mix
  mov #0,r0
  mov.l r0,@(8,r1)
  mov.l @({VSEL:#x},pc),r2
  mov.b r0,@r2
  mov.l @(44,r11),r12       ; current position
  mov #0,r13
  mov r11,r4
  mov #-128,r5
  mov #2,r6
  mov.l @({FSEEK3:#x},pc),r0
  jsr @r0                   ; fseek(file, -128, END)
  nop
  tst r0,r0
  bf vrest
  mov.l @({VSCR:#x},pc),r4
  mov #1,r5
  mov #64,r6
  add r6,r6
  mov r11,r7
  mov.l @({FREAD:#x},pc),r0
  jsr @r0                   ; fread(scratch, 1, 128, file)
  nop
  mov r0,r13
vrest:
  mov r11,r4
  mov r12,r5
  mov #0,r6
  mov.l @({FSEEK3:#x},pc),r0
  jsr @r0                   ; fseek(file, pos, SET): put it back
  nop
  mov #64,r0
  add r0,r0
  cmp/eq r13,r0             ; whole trailer read?
  bf vnorm
  mov.l @({VSCR:#x},pc),r1
  mov.l @r1,r0
  mov.l @({MAGIC:#x},pc),r2
  cmp/eq r2,r0
  bf vnorm                  ; not a version file
  mov.l @({VTBL:#x},pc),r2
  mov.l @(4,r1),r0
  mov.l r0,@r2              ; data_off
  mov.l @(8,r1),r0
  mov.l r0,@(4,r2)          ; data_len
  mov #16,r3
  add r3,r1                 ; -> rel[0]
  mov #12,r3
  add r2,r3                 ; -> table rel[0]
  mov #8,r6
vcp:
  mov.l @r1+,r0
  mov.l r0,@r3
  add #4,r3
  dt r6
  bf vcp
  mov.l @({VSCR:#x},pc),r1
  mov.l @(12,r1),r0         ; n (u16) + 0
  mov.l r0,@(8,r2)          ; publish n last
vhave:
  mov.l @({VTBL:#x},pc),r1
  mov.l @({VSEL:#x},pc),r0
  mov.b @r0,r0
  extu.b r0,r0
  mov.l @(8,r1),r2          ; n
  extu.w r2,r2              ; (u16: the high half is the keycode, not a count)
  cmp/hi r2,r0              ; version beyond this file's count?
  bt vnorm
  mov.l @(44,r11),r12       ; pos
  mov.l @r1,r3              ; data_off
  cmp/hs r3,r12
  bf vnorm                  ; header area
  mov.l @(4,r1),r2
  add r3,r2                 ; data end
  cmp/hs r2,r12
  bt vnorm                  ; past the sample data
  add #-1,r0
  shll2 r0
  add r1,r0
  mov.l @(12,r0),r13        ; rel[k-1]
  mov r11,r4
  mov r12,r5
  add r13,r5
  mov #0,r6
  mov.l @({FSEEK3:#x},pc),r0
  jsr @r0                   ; fseek(file, pos + rel, SET)
  nop
  tst r0,r0
  bf vnorm                  ; seek failed: stock read at pos
  mov r8,r4
  mov r9,r5
  mov r10,r6
  mov r11,r7
  mov.l @({FREAD:#x},pc),r0
  jsr @r0                   ; read the chosen version's bytes
  nop
  mov r0,r13
  mul.l r13,r9
  sts macl,r5
  add r12,r5
  mov r11,r4
  mov #0,r6
  mov.l @({FSEEK3:#x},pc),r0
  jsr @r0                   ; fseek(file, pos + bytes read, SET)
  nop
  bra vout
  mov r13,r0
vnorm:
  mov r8,r4
  mov r9,r5
  mov r10,r6
  mov r11,r7
  mov.l @({FREAD:#x},pc),r0
  jsr @r0
  nop
vout:
  mov.l @r15+,r13
  mov.l @r15+,r12
  mov.l @r15+,r11
  mov.l @r15+,r10
  mov.l @r15+,r9
  mov.l @r15+,r8
  lds.l @r15+,pr
  rts
  nop
"""


def code():
    c = assemble(SRC)
    assert VCAVE + len(c) <= VDATA, len(c)
    return c


def plan():
    c = code()
    return [(VCAVE + i, b) for i, b in enumerate(c)] + \
        [(HOOK + i, b) for i, b in enumerate(struct.pack("<I", VCAVE))]


def emu(version=2):
    """Build a 90 s version file, mount it, browse, load with VSEL set."""
    import numpy as np
    from emu.machine import Machine
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    from emu import panl
    import versionfile as VF
    root = "/tmp/vstick"
    d = os.path.join(root, "Contents", "VerTest")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "V versions.wav")
    if not os.path.exists(out):
        src = os.path.expanduser(
            "~/Downloads/Rekordbox stuff/Contents/Dave Armstrong/UnknownAlbum/"
            "Dave Armstrong - Make Your Move (Original M.aiff")
        tmp = "/tmp/vstick_src"
        os.makedirs(tmp, exist_ok=True)
        mix = os.path.join(tmp, "mix.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "60", "-t", "60",
                        "-i", src, "-c:a", "pcm_s16le", mix], check=True)
        stems = []
        for nm, af in (("b", "lowpass=150"), ("d", "highpass=150,lowpass=2500"),
                       ("v", "highpass=300,lowpass=3400"), ("o", "highpass=3400")):
            p = os.path.join(tmp, nm + ".wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mix, "-af", af, p],
                           check=True)
            stems.append(p)
        print("versions:", VF.build(mix, out, stems=stems, keys=(2,)))
    d_off, d_len, V = VF.read_versions(out)
    names = list(V)
    want = V[names[version]]
    m = Machine.load("cache/emu_realusb.pkl")
    dev, mem = m.mem.dev, m.mem
    u = dev._usb()
    u.unplug()
    m.cpu.run(limit=20_000_000)
    disk = open_disk(root)
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(6):
        m.cpu.run(limit=250_000_000)

    def key(k, n=15_000_000):
        dev.panel_frame = panl.build_frame(keys=[k])
        m.cpu.run(limit=3_000_000)
        dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=n)
    key("Browse")
    mem.w16(0x0B6C7B48, (mem.r16(0x0B6C7B48) + 1) & 0xFFFF)
    m.cpu.run(limit=15_000_000)
    for _ in range(3):
        key("RotaryPush", 30_000_000)       # [FOLDER] -> Contents -> VerTest
    for a, b in plan():
        mem.w8(a, b)
    mem.w8(VSEL, 0)
    # switch to the py core for tracing the reads during the load
    m.save("/tmp/vstick_ready.pkl")
    m = Machine.load("/tmp/vstick_ready.pkl", core="py")
    mem, dev, c = m.mem, m.mem.dev, m.cpu
    pend, res = {}, []

    def tr(pc, op):
        if pc == VCAVE:                      # hook entry: args all set
            if mem.r32(VTBL + 8) and mem.r8(VSEL) == 0 and not pend.get("set"):
                mem.w8(VSEL, version)        # "press" once the file is known
                pend["set"] = 1
            fp = c.gr(7) & 0x1FFFFFFF
            pend["pos"] = mem.r32(fp + 0x2C)
            pend["buf"] = c.gr(4) & 0x1FFFFFFF
        if pc == 0x08D5FD42 and "pos" in pend:   # back from the read
            pos, n = pend.pop("pos"), c.gr(0)
            buf = pend.pop("buf")
            got = bytes(mem.r8(buf + i) for i in range(min(n, 256)))
            if d_off <= pos < d_off + d_len and mem.r8(VSEL) == version:
                exp = want[pos - d_off:pos - d_off + len(got)]
                mix = V["mix"][pos - d_off:pos - d_off + len(got)]
                res.append((pos, got == exp, got == mix))
    c.trace = tr
    dev.panel_frame = panl.build_frame(keys=["RotaryPush"])
    c.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    c.run(limit=int(os.environ.get("STEPS", "80000000")))
    c.trace = None
    hits = sum(1 for _, e, _m in res if e)
    print(f"version {version} ({names[version]}): {len(res)} audio reads, "
          f"{hits} returned the chosen version's bytes, "
          f"{sum(1 for _, e, mm in res if mm and not e)} returned the mix")
    ok = len(res) > 0 and hits == len(res)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["plan"]:
        print(f"vcave {len(code())} B @ {VCAVE:#x}; hook {HOOK:#x} "
              f"{FREAD:#x} -> {VCAVE:#x}; data @ {VDATA:#x}")
        return 0
    return emu(int(os.environ.get("VER", "2")))


if __name__ == "__main__":
    sys.exit(main())
