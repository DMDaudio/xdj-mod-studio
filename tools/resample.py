#!/usr/bin/env python3
"""resample.py -- linear-interpolating resampler in SH-4 (shasm).

Key-shift front end (pv.py stage 1): arbitrary-ratio int16 mono
resampling. Q16 phase, differential form with a 64-bit product so the
rounding is bit-exact vs the python model:

  out = x0 + (((x1-x0) * f + 32768) >> 16),  f = phase & 0xFFFF

  in:  r4=DST (int16*), r5=SRC (int16*), r6=N (out count),
       r7=STEP (Q16 input advance per output; 0x10000 = 1.0x)
  out: r4 advanced past last sample. Clobbers r0-r3. Ends rts+nop.
  Caller guarantees SRC holds ceil(N*STEP/65536)+1 samples.

Run: .venv/bin/python tools/resample.py (self-test vs model, incl. a
real 440->880Hz sweep and irrational ratio).
"""
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code

SRC = """
resample:
  tst r6, r6
  bt done
  mov #0, r1
samp:
  mov r1, r0
  shlr16 r0
  add r0, r0
  add r5, r0
  mov.w @r0, r2
  add #2, r0
  mov.w @r0, r3
  mov r1, r0
  extu.w r0, r0
  sub r2, r3
  dmuls.l r3, r0
  sts macl, r0
  sts mach, r2
  mov #64, r3
  shll r3
  shll8 r3
  add r3, r0
  cmp/hi r0, r3
  movt r3
  add r3, r2
  shlr16 r0
  shll16 r2
  or r2, r0
  mov r1, r3
  shlr16 r3
  add r3, r3
  add r5, r3
  mov.w @r3, r2
  add r0, r2
  mov r2, r0
  mov.w r0, @r4
  add #2, r4
  add r7, r1
  dt r6
  bf samp
done:
  rts
  nop
"""

OUT, SRCB = 0x0B200000, 0x0B210000


def model(src, n, step):
    out = []
    for j in range(n):
        pos = j * step
        i, f = pos >> 16, pos & 0xFFFF
        x0, x1 = src[i], src[i + 1]
        out.append(x0 + (((x1 - x0) * f + 32768) >> 16))
    return out


def run_case(src, n, step):
    import struct
    code = assemble(SRC)
    c, mem, res = run_code(code, regs={4: OUT, 5: SRCB, 6: n, 7: step},
                           data={SRCB: struct.pack("<%dh" % len(src), *src)})
    out = []
    for i in range(n):
        v = mem.r16(OUT + 2 * i)
        out.append(v - 65536 if v >= 32768 else v)
    return out, res


def main():
    import math
    # 1. unity (step=1.0): exact copy incl. edges
    src = [int(30000 * math.sin(i * 0.3)) for i in range(200)]
    out, res = run_case(src, 100, 0x10000)
    assert out == src[:100], (out[:4], res)
    print("unity exact", res)
    # 2. half rate (step=2.0): even samples exact
    out, _ = run_case(src, 50, 0x20000)
    assert out == src[::2][:50], out[:4]
    print("half-rate exact")
    # 3. irrational ratio vs model
    src3 = (src * 3)[:600]
    out, _ = run_case(src3, 300, 0x1A2B3)
    want = model(src3, 300, 0x1A2B3)
    assert out == want, ([a - b for a, b in zip(out, want)][:6])
    print("irrational ratio bit-exact vs model")
    # 4. sweep vs model (real audio-ish)
    sw = [int(28000 * math.sin(2 * math.pi * (440 + i * 2) * i / 44100))
          for i in range(400)]
    out, _ = run_case(sw, 200, 0x18000)
    want = model(sw, 200, 0x18000)
    bad = sum(1 for a, b in zip(out, want) if a != b)
    assert bad == 0, bad
    print("sweep bit-exact (200/200)")
    print("RESAMPLER: 4/4 PASS")


if __name__ == "__main__":
    main()
