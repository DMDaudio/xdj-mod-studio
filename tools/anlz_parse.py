#!/usr/bin/env python3
"""anlz_parse.py -- ANLZ section finder in SH-4 (shasm), the RGB parser core.

Finds a tagged section (e.g. PWV5) in a rekordbox ANLZ buffer already in
RAM and returns its column data. File integers are big-endian; the SH is
little-endian, so lengths convert byte-wise. Tag compare is byte-wise
(mov.b + cmp/eq #imm -- all four PWV5 bytes fit shasm immediates).

  in:  r4 = buf, r5 = buflen, r6 = tag byte0..3 packed (r6 ignored; PWV5
       is assembled inside -- search tag is fixed, see TAG comment)
  out: r0 = column ptr (0 if not found), r1 = ncols
  clobbers r0-r3. Ends rts+nop.

Run: .venv/bin/python tools/anlz_parse.py  (self-test on the real EXT:
finds PWV5, ptr+count exact; negative test on DAT-without-PWV5.)
"""
import os
import struct
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code

# TAG PWV5 = bytes 50 57 56 35.
# r4 = pos (starts at buf), r5 = end (buf+len). Loop while end-pos > 12.
SRC = """
find:
  add #28, r4
loop:
  mov r5, r0
  sub r4, r0
  mov #12, r3
  cmp/hs r0, r3
  bt fail
  mov r4, r7
  mov.b @r4+, r0
  cmp/eq #0x50, r0
  bf next
  mov.b @r4+, r0
  cmp/eq #0x57, r0
  bf next
  mov.b @r4+, r0
  cmp/eq #0x56, r0
  bf next
  mov.b @r4+, r0
  cmp/eq #0x35, r0
  bf next
found:
  mov.b @r4+, r0
  mov.b @r4+, r1
  mov.b @r4+, r2
  mov.b @r4+, r3
  extu.b r0, r0
  extu.b r1, r1
  extu.b r2, r2
  extu.b r3, r3
  shll16 r0
  shll8 r0
  shll16 r1
  shll8 r2
  or r1, r0
  or r2, r0
  or r3, r0
  mov r0, r6
  mov r4, r7
  add r6, r7
  add #-8, r7
  mov.b @r4+, r0
  mov.b @r4+, r1
  mov.b @r4+, r2
  mov.b @r4+, r3
  extu.b r0, r0
  extu.b r1, r1
  extu.b r2, r2
  extu.b r3, r3
  shll16 r0
  shll8 r0
  shll16 r1
  shll8 r2
  or r1, r0
  or r2, r0
  or r3, r0
  mov r0, r1
  sub r6, r1
  shar r1
  mov r7, r0
  rts
  nop
next:
  mov r7, r4
  add #8, r4
  mov.b @r4+, r0
  mov.b @r4+, r1
  mov.b @r4+, r2
  mov.b @r4+, r3
  extu.b r0, r0
  extu.b r1, r1
  extu.b r2, r2
  extu.b r3, r3
  shll16 r0
  shll8 r0
  shll16 r1
  shll8 r2
  or r1, r0
  or r2, r0
  or r3, r0
  mov r4, r1
  add r0, r1
  add #-12, r1
  mov r1, r4
  bra loop
  nop
fail:
  mov #0, r0
  mov #0, r1
  rts
  nop
"""

BUF = 0x0B200000


def run_case(blob):
    code = assemble(SRC)
    c, mem, res = run_code(code, regs={4: BUF, 5: BUF + len(blob)},
                           data={BUF: blob})
    return c.b0[0], c.b0[1], res


def main():
    d = open(os.path.expanduser("~/Downloads/Rekordbox stuff/PIONEER/USBANLZ/"
                                "P018/000206A8/ANLZ0000.EXT"), "rb").read()
    ptr, n, res = run_case(d)
    pos = 28
    want_ptr, want_n = None, None
    while pos + 12 <= len(d):
        tag = d[pos:pos + 4]
        lh, lt = struct.unpack(">II", d[pos + 4:pos + 12])
        if tag == b"PWV5":
            want_ptr, want_n = BUF + pos + lh, (lt - lh) // 2
            break
        if lt <= 0:
            break
        pos += lt
    assert (ptr, n) == (want_ptr, want_n), (hex(ptr), n, hex(want_ptr))
    print(f"PWV5 found: ptr={ptr:#x} ncols={n} {res}")
    # negative: DAT section list has no PWV5 here (use header-only blob)
    ptr2, n2, _ = run_case(d[:28] + b"PPTH" + struct.pack(">II", 16, 216)
                           + b"\0" * 200)
    assert (ptr2, n2) == (0, 0), (hex(ptr2), n2)
    print("negative (no PWV5) exact")
    print("ANLZ PARSER CORE: 2/2 PASS")


if __name__ == "__main__":
    main()
