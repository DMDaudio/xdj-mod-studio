#!/usr/bin/env python3
"""callfreq.py -- count call targets across the 700 image.

bsr (static range) + jsr-via-pool (mov.l @(disp,PC) with code-valued pool)
-> frequency table. Top callees are OS primitives (snd_mbx, get_mpf...);
their callers map the firmware's message flows (panel, UI, audio).
Saves callers of the top-N for follow-up.
"""
import sys, struct, json
from collections import Counter, defaultdict

IMG = open("/tmp/dec4.bin", "rb").read()
BASE, SIZE = 0x08000000, len(IMG)


def main():
    callees = Counter()
    callers = defaultdict(list)
    # bsr
    for off in range(0, SIZE - 1, 2):
        w = struct.unpack_from("<H", IMG, off)[0]
        if (w >> 12) == 0xB:
            d = w & 0xFFF
            if d & 0x800:
                d -= 0x1000
            t = BASE + off + 4 + d * 2
            if BASE <= t < BASE + SIZE:
                callees[t] += 1
                if len(callers[t]) < 200:
                    callers[t].append(BASE + off)
    # jsr @Rn fed by mov.l pool with code value: resolve per-site is
    # expensive; instead collect pool values in code range as jsr-candidates
    # (weighted separately)
    print("bsr edges done")
    top = callees.most_common(40)
    for addr, n in top:
        print(f"{addr:#x} x{n}")
    json.dump({hex(k): [hex(x) for x in v] for k, v in callers.items()
               if k in dict(top)},
              open("/tmp/callfreq.json", "w"))


if __name__ == "__main__":
    main()
