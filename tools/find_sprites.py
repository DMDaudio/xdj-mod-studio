#!/usr/bin/env python3
"""Scan MAIN.blk01.bin for sprite/asset candidates and dump previews.

Entropy map says:
  0x400000-0x5B0000  low ent, 10-27% null, ~30-80 uniq -> bitmaps/fonts
  0xCF0000-0x1180000 mid ent ~6-6.9 -> pixel data?
Strategy: for each 64KB window, report entropy/nulls/uniq plus
top repeated u16/u32 values (palette? dimensions? solid fills).
Dumps RGB565 PPM previews for low-entropy windows so a human can eyeball.
Usage: python3 tools/find_sprites.py work/MAIN.blk01.bin build/sprites/
"""
import math
import os
import struct
import sys
from collections import Counter


def entropy(chunk):
    c = Counter(chunk)
    n = len(chunk)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def rgb565_to_rgb24(v):
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def dump_ppm(path, data, w):
    h = len(data) // (w * 2)
    data = data[: w * h * 2]
    with open(path, "wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode())
        for i in range(0, len(data), 2):
            v = struct.unpack_from("<H", data, i)[0]
            f.write(bytes(rgb565_to_rgb24(v)))


def main(img_path, out_dir):
    d = open(img_path, "rb").read()
    os.makedirs(out_dir, exist_ok=True)
    W = 0x10000
    print(f"{'off':>9} {'ent':>5} {'null%':>6} uniq top-u16(repeats)")
    for off in range(0, len(d), W):
        chunk = d[off:off + W]
        ent = entropy(chunk)
        nulls = chunk.count(0) / len(chunk)
        c16 = Counter(struct.unpack_from(f"<{len(chunk)//2}H", chunk))
        top = c16.most_common(4)
        uniq = len(Counter(chunk))
        flag = ""
        if 0x400000 <= off < 0x5C0000 or 0xCF0000 <= off < 0x1180000:
            flag = "  <-- ASSET?"
        print(f"{off:#09x} {ent:5.2f} {nulls:6.1%} {uniq:4} "
              f"{' '.join(f'{v:#06x}x{n}' for v, n in top)}{flag}")
        if flag and ent < 6.5:
            dump_ppm(f"{out_dir}/rgb565_{off:#08x}_w256.ppm", chunk, 256)
    print(f"\npreviews in {out_dir}/ (open .ppm with Preview/GIMP)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "work/MAIN.blk01.bin",
         sys.argv[2] if len(sys.argv) > 2 else "build/sprites")
