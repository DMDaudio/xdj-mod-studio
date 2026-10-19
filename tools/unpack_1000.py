#!/usr/bin/env python3
"""Unpack XDJ-1000MK2 MAIN: S-records -> flash image -> LZSS -> unpacked.

Ground-truth oracle (Thomas-Geraghty/pioneer-dj-usb-shell data/firmware.json):
  input  XDJ1KMK2.UPD sha256 6fd2a500...02dbcd
  packed application offset in decoded flash: 0x40000
  unpacked 29,275,684 bytes, sha256 46aaaed9...7df, base 0x08000000.

Tries the 700's LZSS (tools/lzss_boot, same Pioneer 4KiB-window family)
at several header skips; the hash decides.
"""
import sys, hashlib
sys.path.insert(0, ".")
from tools.lzss_boot import decode as L

UPD = "XDJ-1000/XDJ1KMK2.UPD"
OUT = "XDJ-1000/MAIN1000.img"
WANT_IN = "6fd2a5002b297b86fb8a5931d3debf1b607ff6abaf9d49cd737d3a530002dbcd"
WANT_OUT = "46aaaed98179157c523cd2797ecd3223862387a8baec21c9d4dffba87a1237df"
WANT_LEN = 29275684


def main():
    raw = open(UPD, "rb").read()
    h = hashlib.sha256(raw).hexdigest()
    print("input sha256:", h, "MATCH" if h == WANT_IN else "MISMATCH")
    if h != WANT_IN:
        return 1
    flash = bytearray(b"\xff" * 0xB00000)
    n = 0
    prev = -1
    for line in raw.split(b"\n"):
        if not line.startswith(b"S2"):
            continue
        rec = bytes.fromhex(line[2:].decode("ascii").strip())
        ln = rec[0]
        addr = int.from_bytes(rec[1:4], "big")
        if addr < prev:
            break  # PANL section follows MAIN; different overlay, not flash
        prev = addr
        assert len(rec) == ln + 1
        flash[addr:addr + ln - 4] = rec[4:ln]  # data is ln-4 bytes, NOT ln-5
        n += 1
    print("s2 records (MAIN only):", n)
    packed = bytes(flash[0x40000:0x40000 + 0xB00000])
    print("packed head:", packed[:32].hex(" "))
    import struct
    (plen,) = struct.unpack_from("<I", packed, 0)
    print(f"length prefix: {plen:#x} = {plen}")
    full = L(packed[4:4 + plen])
    cands = {"len-minus-4": L(packed[4:4 + plen - 4]),
             "truncated": full[:WANT_LEN]}
    for name, out in cands.items():
        oh = hashlib.sha256(out).hexdigest()
        mark = "MATCH" if (oh == WANT_OUT and len(out) == WANT_LEN) else ""
        print(f"{name}: len={len(out)} sha={oh[:16]}... {mark}")
        if mark:
            open(OUT, "wb").write(out)
            print("wrote", OUT)
            return 0
    print("no candidate matched - inspect packed header manually")
    return 1


if __name__ == "__main__":
    sys.exit(main())
