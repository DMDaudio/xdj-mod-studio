#!/usr/bin/env python3
"""flac_apply.py [--revert] -- FLAC + Ogg Opus playback on the DECK: our
decoders (dr_flac, libopus fixed-point) behind a virtual-WAV file layer
(tools/xflac/vfile.c; emulator PASS: tools/vfile_emu_test.py, KIND=opus;
Mac: xflac_vfile_test.py + xopus_vfile_test.py). RAM only, reboot =
stock. NOT YET RUN ON HARDWARE. Opus listing replaces the extension
classifier's 4-letter "JPEG" entry with "OPUS" (.jpeg files are no longer
seen as pictures while this is live; rekordbox artwork is .jpg).

Install with NO USB stick in the deck: the deck sorts files into types
when a stick mounts, so the FLAC->WAV type patch must already be in place.

  (no flag)  read the whole area (code + 230 KB work area) first,
             write it (32-bit bursts, read back), then in a quick session
             arm: point the six USB file slots at our layer (LW each,
             verified), patch the two type halfwords (WW), then the two
             read hooks last (fread chains whatever was live: stems /
             versions / stock).
  --revert   everything back to the values it replaced.
"""
import os
import struct
import subprocess
import sys

sys.path[:0] = [".", "tools"]
OUT = "build/xflac"
# extension classifier FUN @0x08BA9740.. returns FLAC/FLA = 5 (known but
# unsupported on the 700); make both return 11 (WAV): one halfword each,
# both in bra delay slots (mov #imm is legal there)
TYPE_PATCH = {0x08BA98F0: (0xE005, 0xE00B), 0x08BA991A: (0xE005, 0xE00B),
              # Ogg Opus: the 4-letter "JPEG" entry (only this classifier
              # reads the list; rekordbox artwork is .jpg) becomes "OPUS"
              # and its result 200 (picture) becomes 11 (WAV)
              0x08BA9988: (0xE0C8, 0xE00B)}
EXT_PATCH = {0x0807781D: (b"JPEG", b"OPUS")}
SLOTS = {"vf_seek": 0x08DE0298, "vf_tell": 0x08DE04FC, "vf_eof": 0x08DE0768,
         "vf_size": 0x08DE0F48, "vf_close": 0x08DDFD28,
         "vf_fread_direct": 0x08DDFFD4, "vf_fread": 0x08D5FFA8}
STOCK = {"vf_fread_direct": 0x08C61A6C, "vf_seek": 0x08C62E1C, "vf_tell": 0x08C632F2,
         "vf_eof": 0x08C63344, "vf_size": 0x08C71124, "vf_close": 0x08C605F4}


def build():
    subprocess.run(["sh", "tools/xflac/build_vfile.sh"], check=True, capture_output=True)
    s = {}
    for line in open(f"{OUT}/vfile.syms"):
        a, _, n = line.split()
        s[n.lstrip("_")] = int(a, 16)
    nm = subprocess.run(["work/sh-tools/bin/sh-elf-nm", f"{OUT}/vfile.elf"],
                        capture_output=True, text=True).stdout
    s["end"] = int([l.split()[0] for l in nm.splitlines() if l.endswith(" _end")][0], 16)
    return open(f"{OUT}/vfile.bin", "rb").read(), s


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    img, s = build()
    base, end = s["vf_entry"], s["end"]
    import hid
    import fastload
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send

    def rd32(h, a):
        return struct.unpack("<I", bytes(br(h, a, 4)))[0]

    def rd16(h, a):
        return struct.unpack("<H", bytes(br(h, a, 2)))[0]

    def rd4(h, a):
        return bytes(br(h, a, 4))

    def put(h, cmd, addr, val):
        q = f"{addr:x},{val:x}{cmd}".encode() + b"\r\x00"
        h.write(q + b"\x00" * (26 - len(q)))
        drain(h, 0.5)

    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    if len(c) != 1:
        sys.exit(f"found {len(c)} shell interface(s)")
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        live = {n: rd32(h, a) for n, a in SLOTS.items()}
        armed = live["vf_fread_direct"] == s["vf_fread_direct"]
        if arg == "--revert":
            if not armed:
                print("not armed - nothing to do")
                return 0
            put(h, "LW", SLOTS["vf_fread"], rd32(h, s["vf_read_prev"]))
            dprev = rd32(h, s["vf_direct_prev"])
            put(h, "LW", SLOTS["vf_fread_direct"], dprev)
            for n, a in SLOTS.items():
                if n not in ("vf_fread", "vf_fread_direct"):
                    put(h, "LW", a, STOCK[n])
            for a, (old, new) in TYPE_PATCH.items():
                put(h, "WW", a, old)
            for a, (old, new) in EXT_PATCH.items():
                for i, b in enumerate(old):
                    put(h, "BW", a + i, b)
            ok = all(rd32(h, a) == STOCK[n] for n, a in SLOTS.items()
                     if n not in ("vf_fread", "vf_fread_direct")) \
                and rd32(h, SLOTS["vf_fread_direct"]) == dprev \
                and all(rd4(h, a) == old for a, (old, new) in EXT_PATCH.items())
            print("RESULT:", "reverted" if ok else "FAIL - REBOOT the deck")
            return 0 if ok else 1
        if armed:
            print("RESULT: PASS (already armed)")
            return 0
        if rd32(h, 0x0BA3C050):
            sys.exit("REFUSED: a track is loaded - eject the USB stick first")
        # the direct-read slot may already belong to the live stems engine
        # (tools/stems_apply.py routes it): we chain to whatever is there
        import stems_apply as SA
        if live["vf_fread_direct"] not in (STOCK["vf_fread_direct"], SA.entry()):
            sys.exit(f"FAIL: direct read slot = {live['vf_fread_direct']:#x}, neither "
                     f"stock nor the stems engine - nothing written")
        for n, a in SLOTS.items():
            if n not in ("vf_fread", "vf_fread_direct") and live[n] != STOCK[n]:
                sys.exit(f"FAIL: {n} slot {a:#x} = {live[n]:#x}, not stock - nothing written")
        for a, (old, new) in TYPE_PATCH.items():
            if rd16(h, a) not in (old, new):
                sys.exit(f"FAIL: type halfword {a:#x} not stock - nothing written")
        for a, (old, new) in EXT_PATCH.items():
            if rd4(h, a) not in (old, new):
                sys.exit(f"FAIL: extension entry {a:#x} not stock - nothing written")
        want = bytearray(img) + bytes(max(0, end - base - len(img)))
        po = s["vf_read_prev"] - base
        want[po:po + 4] = struct.pack("<I", live["vf_fread"])
        pd = s["vf_direct_prev"] - base
        want[pd:pd + 4] = struct.pack("<I", live["vf_fread_direct"])
        left = fastload.install_region(h, base, bytes(want), fastload.SESSION_BUDGET,
                                       allow_live=list(range(s["vf_read_prev"], s["vf_read_prev"] + 4))
                                       + list(range(s["vf_direct_prev"], s["vf_direct_prev"] + 4)))
        if left:
            print(f"RESULT: PARTIAL ({left} B left) - run again")
            return 0
        if fastload.arm_later():
            print("code complete - run again to arm (fresh short session)")
            return 0
        # arm: plain slots, type patch, then the two read hooks last
        for n, a in SLOTS.items():
            if n in ("vf_fread", "vf_fread_direct"):
                continue
            put(h, "LW", a, s[n])
            if rd32(h, a) != s[n]:
                sys.exit(f"FAIL: {n} slot verify - REBOOT the deck")
        for a, (old, new) in EXT_PATCH.items():      # name before its result
            for i, b in enumerate(new):
                put(h, "BW", a + i, b)
            if rd4(h, a) != new:
                sys.exit(f"FAIL: extension entry verify at {a:#x} - REBOOT the deck")
        for a, (old, new) in TYPE_PATCH.items():
            put(h, "WW", a, new)
            if rd16(h, a) != new:
                sys.exit(f"FAIL: type patch verify at {a:#x} - REBOOT the deck")
        for n in ("vf_fread_direct", "vf_fread"):
            put(h, "LW", SLOTS[n], s[n])
            if rd32(h, SLOTS[n]) != s[n]:
                sys.exit(f"FAIL: {n} verify - REBOOT the deck")
        print("RESULT: PASS - insert the stick: FLAC files list and play like WAV")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
