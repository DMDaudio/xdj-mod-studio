#!/usr/bin/env python3
"""Code-first decoder hunt: prologue scan -> walk -> LZSS shape score."""
import sys, struct
sys.path.insert(0, "tools")
from shxray import branch_target
from find1000dec import walk

FLASH = open("/tmp/x1k_flash.bin", "rb").read()[:0x40000]


def prologues():
    """File offsets starting >=3 straight mov.l Rn,@-R15 (or sts.l pr)."""
    out = []
    i = 0
    n = len(FLASH)
    while i < n - 1:
        w = struct.unpack_from("<H", FLASH, i)[0]
        if (w & 0xF00F) == 0x200F and ((w >> 8) & 0xF) == 0xF:
            j, cnt = i, 0
            while j < n - 1:
                wj = struct.unpack_from("<H", FLASH, j)[0]
                if (wj & 0xF00F) == 0x200F and ((wj >> 8) & 0xF) == 0xF:
                    cnt += 1
                    j += 2
                elif wj == 0x4F22:
                    cnt += 1
                    j += 2
                    break
                else:
                    break
            if cnt >= 3:
                out.append(i)
            i = j if j > i else i + 2
        else:
            i += 2
    return out


def main():
    pros = prologues()
    print(len(pros), "prologue sites", flush=True)
    for t in pros:
        code = walk(t, limit=500)
        if not (40 <= len(code) <= 320):
            continue
        txt = "\n".join(f"{m} {o}" for m, o in code.values())
        s = 0
        for kw, pts in [("shll2", 2), ("shlr", 2), ("rotcl", 2), ("mov.b", 1),
                        ("tst", 1), ("extu.b", 2), (" 240", 3), (" 15", 1),
                        ("or ", 1), ("bf ", 1), ("bt ", 1)]:
            if kw in txt:
                s += pts
        if s >= 9:
            print(f"== score {s} func@{t:#x} ({len(code)} insns) ==")
            for pc in sorted(code):
                print(f"  {pc:#x}: {code[pc][0]:10s} {code[pc][1]}")
            print()


if __name__ == "__main__":
    main()
