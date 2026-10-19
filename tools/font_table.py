#!/usr/bin/env python3
"""Walk the nested glyph offset tables at 0xC80000 and dump candidates.

Findings: 15-byte records [e6 00 05 XX]+11 const tail, stride 15, XX steps
~24/8. XX as u16BE 0x05XX points into a second table (+0x504: registros
`ec 3c c3 66 0b 41 XX`, step 0x18=24) and a third (`ee a9 77 ...`, ~24).
24 bytes = 12x16 1bpp glyph. This script dumps each level and renders
24-byte blobs at the chain ends as 12x16 ASCII art.
Usage: python3 tools/font_table.py work/MAIN.blk01.bin
"""
import struct
import sys

TAIL = bytes.fromhex("63 67 10 8d 53 d1 0c ff 7e a5 af")


def render12x16(blob):
    assert len(blob) == 24
    bits = "".join(f"{b:08b}" for b in blob)
    return ["".join("#" if c == "1" else "." for c in bits[r * 12:(r + 1) * 12]) for r in range(16)]


def main(path):
    d = open(path, "rb").read()
    base = 0xC80000
    recs = []
    p = base
    while d[p:p + 11] != TAIL and p < base + 0x20:
        p += 1
    while d[p:p + 11] == TAIL:
        recs.append((p, d[p:p + 15]))
        p += 15
    print(f"level0: {len(recs)} records at {base:#x}")
    for p, r in recs:
        print(f"  {p:#x}: {r.hex(' ')}  off16=0x{r[2]*256+r[3]:05x}")
    t2 = base + 0x504
    print(f"level1 @ {t2:#x}: {d[t2:t2+64].hex(' ')}")
    t3 = base + 0x594
    print(f"level2 @ {t3:#x}: {d[t3:t3+64].hex(' ')}")
    # try: each level1 entry `ec 3c c3 66 0b 41 XX` -> glyph at ??? dump XX run
    print("level1 XX bytes:", end=" ")
    q = t2
    while d[q:q + 6] == bytes.fromhex("ec 3c c3 66 0b 41"):
        print(f"{d[q+6]:#04x}", end=" ")
        q += 8
        if q - t2 > 128:
            break
    print()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "work/MAIN.blk01.bin")
