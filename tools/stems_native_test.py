#!/usr/bin/env python3
"""stems_native_test.py [FILE] -- the live-stems mixer (tools/stems/sm.c,
built natively with mock fread/fseek) on a real STEMS-4 file with the read
sizes the real deck uses (it reads far more than 16 KB at a time --
2026-10-12: zero mixes on hardware with the old 16 KB limit). Every read,
for several masks, must equal stems4.expected() inside the sample data,
the plain file outside it, and leave the file position where a plain read
would."""
import random
import subprocess
import sys
sys.path[:0] = [".", "tools"]
import stems4 as S4                                    # noqa: E402

F = sys.argv[1] if len(sys.argv) > 1 else "dist/deck-tests/Get Lucky ESQUIRE (STEMS).wav"
raw = open(F, "rb").read()
d_off, d_len, big, st, mix = S4.read(F)
rnd = random.Random(5)
reads = [(d_off + 4096, 65536), (d_off + 128, 131072), (d_off - 100, 50000),
         (d_off + 777 * 4 + 2, 40002), (d_off + d_len - 30000, 65536),
         (0, 512), (d_off + d_len + 10, 200)]
reads += [(rnd.randrange(d_off - 2000, d_off + d_len), rnd.choice([9408, 16384, 65536, 262144, 1 << 20]))
          for _ in range(20)]
ok = True
for mask in (0b0001, 0b0101, 0b1110, 0b1111, 0):
    want = S4.expected(st, mask, ">i2" if big else "<i2").tobytes() if mask else mix.tobytes()
    out = subprocess.run(["build/stems/sm_native", F, str(mask)] + [f"{p}:{n}" for p, n in reads],
                         capture_output=True)
    if out.returncode:
        print("mask", mask, "native test error", out.stderr.decode()); ok = False; continue
    data, o, bad = out.stdout, 0, 0
    for pos, n in reads:
        got_n = max(0, min(n, len(raw) - pos))
        got = data[o:o + got_n]
        o += got_n
        exp = bytearray(raw[pos:pos + got_n])
        lo, hi = d_off, d_off + d_len
        a = max(pos, lo); a = lo + ((a - lo + 3) & ~3)
        b = min(pos + got_n, hi); b = lo + ((b - lo) & ~3)
        if mask and a < b:
            exp[a - pos:b - pos] = want[a - lo:b - lo]
        bad += got != bytes(exp)
    print(f"mask {mask:#06b}: {len(reads)} reads (up to 1 MB), {bad} wrong")
    ok &= bad == 0
print("RESULT:", "PASS" if ok else "FAIL")
