#!/usr/bin/env python3
"""Sweep LZSS variants for the XDJ-1000MK2 packed MAIN; oracles: b'Welcome'
present in output, then the published sha256 (46aaaed9..., 29275684 B)."""
import sys, hashlib
sys.path.insert(0, ".")

WANT_OUT = "46aaaed98179157c523cd2797ecd3223862387a8baec21c9d4dffba87a1237df"
WANT_LEN = 29275684


def decode_var(src, prefill, invert, base=3):
    N, F, THR, R0 = 4096, 18, 2, 4096 - 18
    rb = bytearray([prefill]) * R0 + bytearray([0]) * (N - R0)
    r, out, p, n = R0, bytearray(), 0, len(src)
    while p < n:
        fl = src[p]; p += 1
        for _ in range(8):
            if p >= n:
                return bytes(out)
            bit = fl & 1
            is_lit = (bit == 0) if invert else (bit == 1)
            if is_lit:
                c = src[p]; p += 1
                out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
            else:
                if p + 1 >= n:
                    return bytes(out)
                b1, b2 = src[p], src[p + 1]; p += 2
                i = b1 | ((b2 & 0xF0) << 4)
                for k in range((b2 & 0x0F) + base):
                    c = rb[(i + k) & (N - 1)]
                    out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
            fl >>= 1
    return bytes(out)


def main():
    raw = open("XDJ-1000/XDJ1KMK2.UPD", "rb").read()
    flash = bytearray(b"\xff" * 0xB00000)
    for line in raw.split(b"\n"):
        if not line.startswith(b"S2"):
            continue
        rec = bytes.fromhex(line[2:].decode("ascii").strip())
        ln = rec[0]
        addr = int.from_bytes(rec[1:4], "big")
        flash[addr:addr + ln - 4] = rec[4:ln - 1]
    packed = bytes(flash[0x40000:0x40000 + 0xB00000])
    import struct
    (plen,) = struct.unpack_from("<I", packed, 0)
    print("plen:", hex(plen))
    for skip in (4, 8):
        stream = packed[skip:skip + plen]
        for base in (2, 3, 4):
            for prefill in (0x20, 0x00):
                out = decode_var(stream, prefill, False, base)
                wel = b"Welcome" in out
                oh = hashlib.sha256(out).hexdigest()
                match = "HASH MATCH" if (oh == WANT_OUT
                                         and len(out) == WANT_LEN) else ""
                print(f"skip={skip} base={base} prefill={prefill:#04x}: "
                      f"len={len(out)} welcome={wel} sha={oh[:12]} {match}",
                      flush=True)
                if match:
                    open("XDJ-1000/MAIN1000.img", "wb").write(out)
                    print("wrote XDJ-1000/MAIN1000.img")
                    return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
