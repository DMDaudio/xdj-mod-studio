#!/usr/bin/env python3
"""stems_remix_test.py -- prove the re-mix engine (tools/stems/sm.c): after
sequential deck-style reads under one mask, changing the mask rewrites the
already-buffered bytes with the new mix IN PLACE (mask 0 restores the
original bytes bit-exact). Each case: remix_check streams N chunks under
mask A, flips to mask B, dumps the arena; every 4-aligned data byte must
equal stems4.expected(B) (or the raw file for B == 0), and bytes outside
the sample data must be untouched. File-position discipline is enforced
by the helper (exit 2)."""
import os
import subprocess
import sys
sys.path[:0] = [".", "tools"]
import stems4 as S4                                    # noqa: E402

F = "dist/deck-tests/Get Lucky ESQUIRE (STEMS).wav"
raw = open(F, "rb").read()
d_off, d_len, big, st, mix = S4.read(F)
EXE = "build/stems/remix_check"
r = subprocess.run(["cc", "-O2", "-DSM_NATIVE", "-o", EXE,
                    "tools/stems/remix_check.c", "tools/stems/sm.c"],
                   capture_output=True, text=True)
if r.returncode:
    print(r.stderr); print("RESULT: FAIL (build)"); sys.exit(1)

cases = [
    # (start, maskA, maskB, chunk, nchunks): mute more, mute less, unmute all
    (d_off + 65536, 0b0000, 0b0101, 262144, 8),
    (d_off + 65536, 0b0101, 0b1110, 262144, 8),
    (d_off + 65536, 0b0101, 0b0000, 262144, 8),
    (d_off + 65536, 0b0000, 0b1111, 65536, 4),
    (d_off + 100, 0b0011, 0b1100, 131072, 6),   # unaligned start
]
endian = ">i2" if big else "<i2"
ok = True
for start, ma, mb, chunk, nch in cases:
    want = S4.expected(st, mb, endian).tobytes() if mb else None
    p = subprocess.run([EXE, F, str(start), str(ma), str(mb),
                        str(chunk), str(nch)], capture_output=True)
    if p.returncode:
        print(f"A={ma:#06b}->B={mb:#06b}: helper failed: {p.stderr.decode().strip()}")
        ok = False; continue
    got = p.stdout
    info = p.stderr.decode().strip()
    # arena byte i == file byte start+i (reads were sequential from start).
    # The engine only rewrites whole frames inside [aligned start, window
    # end); frames outside keep older bytes -- check only what it owns.
    bad = 0
    lo, hi = d_off, d_off + d_len
    b_win = start + len(got)
    a_al = lo + ((max(start, lo) - lo + 3) & ~3)
    for i in range(0, len(got) - 3, 4):       # whole frames only, like the engine
        pos = start + i
        if not (lo <= pos and pos + 4 <= hi):
            if got[i:i + 4] != raw[pos:pos + 4]:
                bad += 1
            continue
        if not (a_al <= pos and pos + 4 <= min(b_win, hi)):
            continue                            # untouched by design
        exp = (raw[pos:pos + 4] if mb == 0
               else want[pos - lo:pos - lo + 4])
        if got[i:i + 4] != exp:
            bad += 1
    # the trigger read's own fresh bytes (tail) are covered above too
    print(f"A={ma:#06b}->B={mb:#06b}: {len(got)} buffered bytes, {bad} wrong; {info}")
    ok &= bad == 0 and b"remix=0 " not in info.encode()
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
