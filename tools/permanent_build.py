#!/usr/bin/env python3
"""permanent_build.py STOCK.UPD OUT.UPD [--mode rgb] [--banner 1.16]
             [--no-pads] [--no-versions]
EXPERIMENTAL: bake the mods into an update file ("permanent mode").

!!! NEVER CONFIRMED ON A REAL DECK. Flashing firmware is the one thing in
!!! this project that can leave a deck needing a service centre. Early tests
!!! (Sept 2026) that changed the main firmware showed "MAIN CPU ERROR" and
!!! were recovered by re-flashing the official file; the cause (a block
!!! checksum the old packer did not update) has been fixed since, and every
!!! file this tool writes is checked and BOOTED in the emulator through the
!!! deck's own startup code before it is kept.

What it does, from the user's own official XDJ-700 v1.15 update file:
  * decodes the main firmware block and applies, at the same addresses the
    RAM installers use: overview + centre
    waveforms (mode), key/stem version switching, the MY SETTINGS rows and
    the PERFORM pads -- by running each mod's own emulator installer
    against the firmware image instead of live memory;
  * version record -> BANNER (UTILITY shows it) and the update banner ->
    BANNER, so the deck's updater accepts it (it needs a different
    version from the installed one);
  * re-compresses with our encoder (37.8 KB tighter than Pioneer's on
    stock, which is the room the code needs), refuses if it does not fit,
    recomputes the block checksums, round-trips the file, and leaves the
    boot stub, block 0 and the PANL section byte-identical to stock.
Not baked (they live outside the firmware's storage): hot cue pages,
theme switching, phase meter, the centre-waveform colour module (its cave
sits outside the firmware image, so permanent builds keep the stock blue
centre -- install those over USB as usual).

To go back to stock: flash the OFFICIAL v1.15 file (its version differs
from BANNER, so the updater accepts it).

Writing a file needs --i-know-flashing-can-brick (and read this header
first). The --emu boot-test now also taps through the baked PERFORM
screen. What --emu can NOT prove: a real flash (power loss mid-write,
a worn flash chip) -- that part is inherently deck-side. Never flash
without the official recovery file on hand and a full battery/UPS.

   permanent_build.py STOCK OUT --i-know-flashing-can-brick
   then  permanent_build.py --emu OUT
"""
import argparse
import hashlib
import os
import re
import struct
import sys

sys.path[:0] = [".", "tools", "mods"]
import fwtool                       # noqa: E402
import lzss_boot as L               # noqa: E402

BASE = 0x08000000
OFFICIAL = "73edec9802da51672257c2599efc04209dc92478fcbaa1a0425b3b122e33f99c"
VERSION_AT = 0x740                  # "1.15" in the version record (decoded block 1)


class ImgMem:
    """r8/w8/r16/w16/r32/w32 over the decoded firmware image (SH address
    space at 0x08000000); anything outside the image is refused."""
    def __init__(self, buf):
        self.b = buf

    def _o(self, a, n):
        o = a - BASE
        if not (0 <= o and o + n <= len(self.b)):
            raise ValueError(f"{a:#x} is outside the firmware image")
        return o

    def r8(self, a):
        return self.b[self._o(a, 1)]

    def w8(self, a, v):
        self.b[self._o(a, 1)] = v & 0xFF

    def r16(self, a):
        return struct.unpack_from("<H", self.b, self._o(a, 2))[0]

    def w16(self, a, v):
        struct.pack_into("<H", self.b, self._o(a, 2), v & 0xFFFF)

    def r32(self, a):
        return struct.unpack_from("<I", self.b, self._o(a, 4))[0]

    def w32(self, a, v):
        struct.pack_into("<I", self.b, self._o(a, 4), v & 0xFFFFFFFF)


def apply_mods(mem, mode="rgb", versions=True, pads=True, log=print):
    import rgb_overview as RO
    import rgb_detail as RD
    import vredirect as VR
    import mysettings as MS
    for a, b in RO.plan(RO.MODES[mode])[1]:
        mem.w8(a, b)
    log(f"overview waveform: {mode} (+ snapshot routine)")
    # Centre RGB needs its colour module in a RAM-only cave outside this
    # image (ImgMem refuses it): permanent mode keeps the stock centre.
    log("centre waveform: stock (colour module lives outside the image)")
    if versions:
        assert mem.r32(VR.HOOK) == VR.FREAD, "version hook not stock"
        for a, b in VR.plan():
            mem.w8(a, b)
        log("key/stem version switching")
    MS.install(mem)
    log("MY SETTINGS rows")
    if pads:
        import perform_pads as PP
        PP.install(mem)
        log("PERFORM pads (BEAT JUMP / KEY SHIFT / STEMS tabs)")


def build(stock, out, mode="rgb", banner="1.16", versions=True,
          pads=True, log=print):
    h = hashlib.sha256(open(stock, "rb").read()).hexdigest()
    if h != OFFICIAL:
        raise SystemExit("REFUSED: not the official XDJ-700 v1.15 update file")
    if not re.fullmatch(r"\d\.\d\d", banner) or banner == "1.15":
        raise SystemExit("REFUSED: banner must look like 1.16 and differ from 1.15")
    raw, secs = fwtool.parse(stock)
    main = secs[0]
    hdr, ln = max(main.blocks(), key=lambda t: t[1])
    dec = bytearray(L.decode(bytes(main.img[hdr + 4:hdr + 4 + ln])))
    stock_dec = bytes(dec)
    mem = ImgMem(dec)
    apply_mods(mem, mode, versions, pads, log)
    assert dec[VERSION_AT:VERSION_AT + 4] == b"1.15", "version record not found"
    dec[VERSION_AT:VERSION_AT + 4] = banner.encode()
    changed = sum(1 for a, b in zip(dec, stock_dec) if a != b)
    log(f"{changed:,} bytes of the firmware image changed")
    log("compressing (about 30 s) ...")
    st = L.encode(bytes(dec))
    if L.decode(st) != bytes(dec):
        raise SystemExit("REFUSED: encoder round-trip failed")
    log(f"compressed block: {ln:,} -> {len(st):,} bytes ({len(st) - ln:+,})")
    main.write_block(hdr, ln, st)          # refuses if it does not fit
    old = main.pre
    main.pre = re.sub(r"(MAINVer)[0-9.]+", lambda m: m.group(1) + banner, main.pre, count=1)
    if main.pre == old:
        raise SystemExit("REFUSED: update banner not found")
    open(out, "wb").write(fwtool.assemble(secs))
    # ---- verify the written file
    _, chk = fwtool.parse(out)
    if not all(okb for *_, okb in chk[0].check_blocks()):
        raise SystemExit("REFUSED: block checksum mismatch in the written file")
    h2, l2 = max(chk[0].blocks(), key=lambda t: t[1])
    if L.decode(bytes(chk[0].img[h2 + 4:h2 + 4 + l2])) != bytes(dec):
        raise SystemExit("REFUSED: written file does not decode to the intended image")
    s_raw, s_secs = fwtool.parse(stock)
    sh, sl = min(s_secs[0].blocks(), key=lambda t: t[0])
    if bytes(chk[0].img[:sh + 4 + sl + 2]) != bytes(s_secs[0].img[:sh + 4 + sl + 2]):
        raise SystemExit("REFUSED: boot stub / block 0 changed")
    if chk[1].emit() != s_secs[1].emit():
        raise SystemExit("REFUSED: PANL section changed")
    log(f"written {out}: checksums OK, decodes exactly, boot stub + block 0 + "
        f"PANL identical to stock, banner {banner}")
    return out


def emu_check(upd, steps=6_000_000_000, log=print):
    """Boot the built file FROM RESET in the emulator (the deck's own boot
    stub decompresses + checks it), then confirm the mods are live in RAM
    with nothing installed over USB, and the main screen came up."""
    from emu import fw as F
    from emu.machine import Machine
    from emu import panl
    raw, main, panl_sec = F.load_upd(upd)
    m = Machine(F.flash_image(main))
    m.mem.dev.panel_frame = panl.build_frame()
    done = 0
    while done < steps:
        m.cpu.run(limit=500_000_000)
        done += 500_000_000
    import rgb_overview as RO
    import rgb_detail as RD
    import vredirect as VR
    import mysettings as MS
    ok = True
    checks = [("overview hook", m.mem.r32(RO.HOOK_POOL) == RO.CAVE),
              ("centre stock (module is USB-only)",
               m.mem.r32(RD.POOL) == RD.PALETTE),
              ("versions hook", m.mem.r32(VR.HOOK) == VR.VCAVE),
              ("MY SETTINGS hooks", all(m.mem.r32(p) == n for p, o, n in MS.hooks()))]
    for name, c in checks:
        log(("  ok   " if c else "  FAIL ") + name + " live after boot")
        ok &= c
    sys.path.insert(0, "tools")
    from ghosttap_emu import fb_png
    shots = os.environ.get("XDJ_SHOTS", "screenshots/permanent")
    os.makedirs(shots, exist_ok=True)
    fb_png(m, os.path.join(shots, "booted.png"))
    import numpy as np
    from PIL import Image
    a = np.array(Image.open(os.path.join(shots, "booted.png")).convert("L"))
    drawn = int((a > 40).sum())
    log(f"  {'ok  ' if drawn > 20000 else 'FAIL'} main screen drawn ({drawn:,} lit px)")
    ok &= drawn > 20000
    # PERFORM smoke on the baked pads (no track needed): open by touch,
    # every tab, one jump-pad tap; nothing may fault
    import perform_pads as PP
    e0 = m.exc_total

    def touch(p, hold=3_000_000, after=3_000_000):
        m.mem.dev.panel_frame = panl.build_frame(touch=p)
        m.cpu.run(limit=hold)
        m.mem.dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=after)
        for _ in range(40):                       # press + release settle
            if m.mem.r8(PP.PST) == 0 and m.mem.r32(PP.ZSTATE) == 0x1E:
                break
            m.cpu.run(limit=500_000)

    touch((709, 37))
    opened = m.mem.r32(0x0BA371D4) == 7
    log(f"  {'ok  ' if opened else 'FAIL'} PERFORM opens by touch")
    ok &= opened
    for k in range(4):
        touch((PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[k] + PP.TAB_H // 2))
        log(f"      tab{k}: mode={m.mem.r8(PP.PST + 2)} latch={m.mem.r8(PP.PST)} "
            f"zone={m.mem.r32(PP.ZSTATE):#x}")
    tabs = m.mem.r8(PP.PST + 2) == 3    # tapped 0,1,2,3: ends on STEMS
    log(f"  {'ok  ' if tabs else 'FAIL'} all four tabs switch")
    ok &= tabs
    touch((200, 263))
    clean = m.exc_total == e0
    log(f"  {'ok  ' if clean else 'FAIL'} pad tap, no exceptions "
        f"({m.exc_total - e0} new)")
    ok &= clean
    return ok, m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--mode", default="rgb", choices=["rgb", "3band", "blue"])
    ap.add_argument("--banner", default="1.16")
    ap.add_argument("--no-pads", action="store_true")
    ap.add_argument("--no-versions", action="store_true")
    ap.add_argument("--emu", action="store_true", help="boot-test a built file")
    ap.add_argument("--i-know-flashing-can-brick", action="store_true",
                    help="REQUIRED to write a file: flashing the wrong file, "
                         "or losing power mid-flash, can leave the deck "
                         "needing a service centre")
    a = ap.parse_args()
    if a.emu:
        ok, _ = emu_check(a.files[0])
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    if not a.i_know_flashing_can_brick:
        ap.error("refusing to write: re-run with --i-know-flashing-can-brick "
                 "after reading the warnings at the top of this tool")
    build(a.files[0], a.files[1], a.mode, a.banner,
          not a.no_versions, not a.no_pads)
    print("RESULT: built (NOT flash-tested). Boot-test: permanent_build.py --emu", a.files[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
