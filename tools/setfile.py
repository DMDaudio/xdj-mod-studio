#!/usr/bin/env python3
"""setfile.py -- read, diff and patch Pioneer MYSETTING/DEVSETTING files.

    setfile.py show   FILE...            layout + setting bytes
    setfile.py diff   A B                which setting bytes differ
    setfile.py patch  IN OUT OFF=VAL...  set bytes, fix the CRC

WHY THIS EXISTS
The XDJ-700's BACKGROUND COLOR (MY SETTINGS -> BACKGROUND COLOR: DEFAULT, PINK,
RED, ORANGE, YELLOW, GREEN, AQUA, BLUE, PURPLE) is a UI re-tinting feature
Pioneer already shipped. If it is stored in one of these files we can read the
encoding by diffing two saves, then write values back -- including values the
menu does not offer -- WITHOUT flashing anything.

FORMAT
    0x00  u32   header length (0x60)
    0x04  str   brand    "PIONEER" / "PIONEER DJ" / "PioneerDJ"
    0x24  str   software "rekordbox"
    0x44  str   version
    0x64  u32   data length
    0x68  u32   magic 0x12345678
    0x6c  ...   setting bytes (typically 0x80 + value)
    n-4   u16   CRC-16/XMODEM, little-endian

CHECKSUM
CRC-16/XMODEM (poly 0x1021, init 0x0000) over 0x68..n-4 -- i.e. from the magic
up to but not including the CRC. DJMMYSETTING covers the whole file instead.
Verified against all four stock files, first try. Same algorithm the firmware
container uses for its MAIN/PANL sections.
"""
import struct, sys


def crc16x(b):
    c = 0
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def crc_range(data, name):
    """DJMMYSETTING sums the whole file; the others start at the magic."""
    return (0, len(data) - 4) if "DJM" in name.upper() else (0x68, len(data) - 4)


def check(data, name):
    lo, hi = crc_range(data, name)
    return crc16x(data[lo:hi]), struct.unpack_from("<H", data, len(data) - 4)[0]


def show(path):
    d = open(path, "rb").read()
    name = path.split("/")[-1]
    got, want = check(d, name)
    z = lambda o, n: d[o:o + n].split(b"\0")[0].decode("ascii", "replace")
    print(f"\n{name}  {len(d)} bytes")
    print(f"  brand={z(4,32)!r} sw={z(0x24,32)!r} ver={z(0x44,32)!r}")
    print(f"  data len {struct.unpack_from('<I', d, 0x64)[0]:#x}  "
          f"magic {struct.unpack_from('<I', d, 0x68)[0]:#010x}")
    print(f"  CRC computed {got:#06x} stored {want:#06x}  "
          f"{'OK' if got == want else 'MISMATCH'}")
    print("  settings bytes from 0x6c:")
    for r in range(0x6c, len(d) - 4, 16):
        seg = d[r:min(r + 16, len(d) - 4)]
        print(f"    {r:#05x}  {' '.join(f'{x:02x}' for x in seg)}")


def diff(a, b):
    da, db = open(a, "rb").read(), open(b, "rb").read()
    if len(da) != len(db):
        print(f"different lengths {len(da)} vs {len(db)}")
    n = min(len(da), len(db)) - 4
    d = [i for i in range(n) if da[i] != db[i]]
    print(f"{len(d)} setting bytes differ (CRC excluded)")
    for i in d:
        print(f"  {i:#05x}   {da[i]:#04x} -> {db[i]:#04x}"
              f"   ({da[i]-0x80:+d} -> {db[i]-0x80:+d} if 0x80-biased)")
    if not d:
        print("  -> the two saves are identical; the setting is not in this file")


def patch(src, dst, pairs):
    d = bytearray(open(src, "rb").read())
    name = src.split("/")[-1]
    for p in pairs:
        o, v = p.split("=")
        o = int(o, 0); v = int(v, 0)
        print(f"  {o:#05x}: {d[o]:#04x} -> {v:#04x}")
        d[o] = v
    lo, hi = crc_range(d, name)
    c = crc16x(bytes(d[lo:hi]))
    struct.pack_into("<H", d, len(d) - 4, c)
    print(f"  CRC -> {c:#06x}")
    open(dst, "wb").write(bytes(d))
    print(f"  wrote {dst}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "show":
        for f in sys.argv[2:]: show(f)
    elif cmd == "diff":
        diff(sys.argv[2], sys.argv[3])
    elif cmd == "patch":
        patch(sys.argv[2], sys.argv[3], sys.argv[4:])
    else:
        print(__doc__)
