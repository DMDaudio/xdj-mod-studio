#!/usr/bin/env python3
"""pngread.py -- minimal PNG reader (no PIL) + colour census.

    pngread.py FILE.png [TOPN]

WHY: this project has spent its whole life searching for colours it guessed at.
The sim/*.png files are recolourings of a REAL screenshot of the deck, and
simulate.py only rewrites pixels matching palette values -- so almost every
pixel in them is untouched stock UI. That makes them ground truth for what is
actually on screen, which we have never had.

Supports the subset PNG actually needs here: 8-bit RGB/RGBA/grey/palette,
non-interlaced, with the five standard filters.
"""
import struct, sys, zlib, collections


def read(path):
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    p = 8; idat = b""; pal = None; w = h = bd = ct = None
    while p < len(d):
        ln = struct.unpack_from(">I", d, p)[0]; typ = d[p+4:p+8]
        body = d[p+8:p+8+ln]; p += 12 + ln
        if typ == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body)
            assert il == 0, "interlaced PNG not supported"
        elif typ == b"PLTE": pal = body
        elif typ == b"IDAT": idat += body
        elif typ == b"IEND": break
    raw = zlib.decompress(idat)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    assert bd == 8, f"bit depth {bd} not supported"
    stride = w * ch
    out = bytearray(h * stride); prev = bytearray(stride); q = 0
    for y in range(h):
        f = raw[q]; q += 1
        line = bytearray(raw[q:q+stride]); q += stride
        for i in range(stride):
            a = line[i-ch] if i >= ch else 0
            b = prev[i]
            c = prev[i-ch] if i >= ch else 0
            if f == 1: line[i] = (line[i] + a) & 255
            elif f == 2: line[i] = (line[i] + b) & 255
            elif f == 3: line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa = abs(b - c); pb = abs(a - c); pc = abs(a + b - 2*c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y*stride:(y+1)*stride] = line
        prev = line
    px = []
    for i in range(0, len(out), ch):
        if ct == 3:
            j = out[i]*3; px.append((pal[j], pal[j+1], pal[j+2]))
        elif ct in (0, 4): px.append((out[i],)*3)
        else: px.append((out[i], out[i+1], out[i+2]))
    return w, h, px


if __name__ == "__main__":
    w, h, px = read(sys.argv[1])
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    c = collections.Counter(px)
    print(f"{sys.argv[1]}  {w}x{h}  {len(px):,} px, {len(c):,} distinct colours\n")
    print(f"{'RGB888':>8} {'RGB565':>7} {'px':>9} {'%':>6}")
    for (r, g, b), n in c.most_common(top):
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        print(f"  {r:02X}{g:02X}{b:02X} {v:#07x} {n:>9,} {100*n/len(px):>5.2f}")
