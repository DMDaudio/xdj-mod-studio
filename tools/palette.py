#!/usr/bin/env python3
"""palette.py -- the XDJ-700 UI colour tables, and how to read/write them.

All tables sit in block1 between 0xCC2A70 and 0xCC54E0 and are 32-bit entries,
but they do NOT share a byte order. Adjacent tables use different alpha
positions, presumably because different code paths consume them:

    RGBA  alpha in the LOW byte    0xRRGGBBAA   (e.g. 0xCC2AC0)
    ARGB  alpha in the HIGH byte   0xAARRGGBB   (e.g. 0xCC2B14)

Getting that wrong silently skips a table, which is how 0xCC2B14 -- 16 entries
of blues and teals -- was missed on the first pass. So the order is detected
per entry rather than assumed.

NOT CONFIRMED ON HARDWARE. No colour here has been verified on a deck; an
earlier report to that effect was a false positive (the PERFORM button is purple
in stock firmware).

Every entry listed here is literal-encoded in the stock LZSS stream, so it can
be patched with no recompression and no change in stream length.
"""
import struct

# base, entry count, human label
TABLES = [
    (0xCC2A70, 11, "amber / brown ramp"),
    (0xCC2AC0, 20, "gold ramp + blue ramp"),
    (0xCC2B14, 16, "blue ramp + teal/cyan ramp"),
    (0xCC5288, 6,  "near-whites"),
    (0xCC52A8, 5,  "light greys"),
    (0xCC52E0, 5,  "whites"),
    (0xCC5368, 14, "mid greys"),
    (0xCC53F8, 14, "near-blacks (backgrounds)"),
    (0xCC54D8, 5,  "very dark"),
]


def read(dec, o):
    """-> (r, g, b, alpha_value, order) or None if not colour-shaped."""
    v = struct.unpack_from("<I", dec, o)[0]
    if (v & 0xFF) == 0xFF and 0 < (v >> 8) < 0xFFFFFF:
        return ((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, 0xFF, "RGBA")
    if (v >> 24) == 0xFF and 0 < (v & 0xFFFFFF) < 0xFFFFFF:
        return ((v >> 16) & 255, (v >> 8) & 255, v & 255, 0xFF, "ARGB")
    return None


def pack(rgb, alpha, order):
    r, g, b = rgb
    if order == "RGBA":
        return struct.pack("<I", (r << 24) | (g << 16) | (b << 8) | alpha)
    return struct.pack("<I", (alpha << 24) | (r << 16) | (g << 8) | b)


def entries(dec, lit):
    """Yield (offset, rgb, alpha, order, table_base) for every literal-encoded
    colour entry in every known table."""
    for base, count, _ in TABLES:
        for k in range(count):
            o = base + 4 * k
            if not all((o + j) in lit for j in range(4)):
                continue
            c = read(dec, o)
            if c is None:
                continue
            yield o, c[:3], c[3], c[4], base


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from litpatch import map_literals
    img = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "work", "MAIN.img"), "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    dec, lit = map_literals(img[0x40008:0x40008 + ln])
    tot = 0
    for base, count, label in TABLES:
        got = [(o, rgb, order) for o, rgb, a, order, b in entries(dec, lit) if b == base]
        tot += len(got)
        orders = sorted({o for _, _, o in got})
        sample = " ".join(f"{r:02X}{g:02X}{b:02X}" for _, (r, g, b), _ in got[:6])
        print(f"  {base:#08x}  {len(got):>2}/{count:<3} {','.join(orders):<5} {label}")
        print(f"              {sample}")
    print(f"\n  {tot} patchable colour entries across {len(TABLES)} tables")
