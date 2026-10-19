#!/usr/bin/env python3
"""Rank 1000-loader functions by LZSS-decoder shape similarity."""
import sys, struct
sys.path.insert(0, "tools")
from shxray import branch_target
from find1000dec import walk

FLASH = open("/tmp/x1k_flash.bin", "rb").read()[:0x40000]


def main():
    targets = set()
    for i in range(0, len(FLASH) - 1, 2):
        w = struct.unpack_from("<H", FLASH, i)[0]
        if (w >> 12) == 0xB:
            d = w & 0xFFF
            if d & 0x800:
                d -= 0x1000
            t = (i + 4 + d * 2) & 0xFFFFFFFF
            if 0 <= t < len(FLASH):
                targets.add(t)
    scored = []
    for t in sorted(targets):
        code = walk(t, limit=400)
        if not (30 <= len(code) <= 300):
            continue
        txt = "\n".join(f"{m} {o}" for m, o in code.values())
        s = 0
        for kw, pts in [("shll2", 2), ("shll8", 1), ("shlr", 2), ("mov.b", 1),
                        ("tst", 1), ("extu.b", 2), (" 240", 3), (" 15", 1),
                        (" #2", 1), ("or ", 1)]:
            if kw in txt:
                s += pts
        if s >= 9:
            scored.append((s, t, code))
    scored.sort(reverse=True)
    print(len(scored), "candidates")
    for s, t, code in scored[:8]:
        print(f"== score {s} func@{t:#x} ({len(code)} insns) ==")
        for pc in sorted(code):
            print(f"  {pc:#x}: {code[pc][0]:10s} {code[pc][1]}")
        print()


if __name__ == "__main__":
    main()
