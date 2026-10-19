#!/usr/bin/env python3
"""stems_mix.py -- the stems mixing stub (SH-4, shasm).

Contract (locked 2026-09-30): four mono stem buffers + one out buffer,
all int16, SAME rate/length (same decoder), reads locked to the SAME
index (sync is the caller's job: it points all five at the live frame).
Kill byte: bit0=VOC bit1=DRM bit2=BAS bit3=OTH (1 = killed, latching,
set by the UI thread; read once per buffer, not per sample).

  out[i] = clamp16(V[i] + D[i] + B[i] + O[i])   (live stems only)

4x int16 sums fit int32 exactly (max 131068), so one add chain + a final
saturate; no per-add overflow possible. Prologue builds +-32767 without
big immediates (127<<8|255 and -128<<8).

Regs: r4=OUT r5=V r6=NSAMPLES r7=D r8=B r9=O r12=KILLADDR.
Run: .venv/bin/python tools/stems_mix.py  (self-test, no deck, no ckpt)
"""
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code

SRC = """
mix:
  mov.b @r12, r0
  mov #127, r10
  shll8 r10
  mov #-1, r3
  shlr8 r3
  shlr16 r3
  or r3, r10
  mov #-128, r11
  shll8 r11
  tst r6, r6
  bt done
frame:
  mov #0, r1
  mov.w @r5+, r2
  tst #1, r0
  bt keepv
  mov #0, r2
keepv:
  add r2, r1
  mov.w @r7+, r2
  tst #2, r0
  bt keepd
  mov #0, r2
keepd:
  add r2, r1
  mov.w @r8+, r2
  tst #4, r0
  bt keepb
  mov #0, r2
keepb:
  add r2, r1
  mov.w @r9+, r2
  tst #8, r0
  bt keepo
  mov #0, r2
keepo:
  add r2, r1
  cmp/gt r10, r1
  bf nocliphi
  mov r10, r1
nocliphi:
  cmp/ge r1, r11
  bf noclipplo
  mov r11, r1
noclipplo:
  mov.w r1, @r4
  add #2, r4
  dt r6
  bf frame
done:
  rts
  nop
"""

OUT, VBUF, DBUF, BBUF, OBUF, KILL = (0x0B200000, 0x0B210000, 0x0B220000,
                                     0x0B230000, 0x0B240000, 0x0B250000)


def run_case(bufs, kill, n=64):
    import struct
    code = assemble(SRC)
    data = {}
    for addr, samples in bufs.items():
        data[addr] = struct.pack("<%dh" % len(samples), *samples)
    data[KILL] = bytes([kill])
    regs = {4: OUT, 5: VBUF, 6: n, 7: DBUF, 8: BBUF, 9: OBUF, 12: KILL}
    c, mem, res = run_code(code, regs, data)
    out = [mem.r16(OUT + 2 * i) - 65536
           if mem.r16(OUT + 2 * i) >= 32768 else mem.r16(OUT + 2 * i)
           for i in range(n)]
    return out, res


def main():
    n = 64
    loud = [20000] * n
    soft = [1000] * n
    # 1. all live: exact sum
    out, res = run_case({VBUF: loud, DBUF: soft, BBUF: soft, OBUF: soft},
                        0x00)
    assert out == [23000] * n, (out[:4], res)
    print("all-live sum exact:", out[0], res)
    # 2. vocal killed: others only
    out, res = run_case({VBUF: loud, DBUF: soft, BBUF: soft, OBUF: soft},
                        0x01)
    assert out == [3000] * n, (out[:4], res)
    print("vocal kill exact:", out[0], res)
    # 3. all killed: digital silence
    out, res = run_case({VBUF: loud, DBUF: loud, BBUF: loud, OBUF: loud},
                        0x0F)
    assert out == [0] * n, (out[:4], res)
    print("all-kill silence exact", res)
    # 4. saturation: 4x30000 clamps, 4x-30000 clamps low
    out, _ = run_case({VBUF: loud, DBUF: loud, BBUF: loud, OBUF: loud},
                      0x00)
    assert out == [32767] * n, out[:4]
    out, _ = run_case({VBUF: [-30000] * n, DBUF: [-30000] * n,
                       BBUF: [-30000] * n, OBUF: [-30000] * n}, 0x00)
    assert out == [-32768] * n, out[:4]
    print("saturation exact: +32767 / -32768")
    print("STEMS MIX STUB: 4/4 PASS")


if __name__ == "__main__":
    main()
