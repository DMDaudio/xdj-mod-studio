#!/usr/bin/env python3
"""panl_fullmap.py CKPT OUT.json -- full panel-struct map.

Like panl_oracle.py but dumps a 256-byte window around the panel struct
(0x0B6C7A00-0x0B6C7B00) per synthetic payload bit, recording EVERY field
the parser touches -- not just key bytes 84..107. Touch XY would land as
byte FIELDS here if it rides PANL.
"""
import sys, json
sys.path.insert(0, ".")
from emu.machine import Machine
from emu.sh4 import PcHalt

m = Machine.load(sys.argv[1], core="py")
c, mem = m.cpu, m.mem
PAY = 0x0B6C7A30
BASE, SIZE = 0x0B6C7A00, 0x100
MAGIC = 0x08000118
snap_ram0 = bytes(mem.ram0)
snap_ram1 = bytes(mem.ram1)
regs = (list(c.b0), list(c.b1), c.sr, c.pc, c.pr, c.gbr)


def restore():
    mem.ram0[:] = snap_ram0
    mem.ram1[:] = snap_ram1
    c.b0[:], c.b1[:] = regs[0], regs[1]
    c.sr, c.pc, c.pr, c.gbr = regs[2], regs[3], regs[4], regs[5]


def call(payload):
    restore()
    for i, b in enumerate(payload):
        mem.w8(PAY + i, b)
    c.sr |= 0x10000000
    c.pr = MAGIC
    c.pc = 0x08D51A64

    def tr(pc, op):
        if pc == MAGIC:
            raise PcHalt(pc)
    c.trace = tr
    try:
        c.run(limit=200000)
    finally:
        c.trace = None
    return bytes(mem.r8(BASE + k) for k in range(SIZE))


base = call(bytes(30))
print("baseline keys84..107:", base[0x28 + 84:0x28 + 108].hex())
out = {}
for byte in range(0, 30):
    for bit in range(8):
        p = bytearray(30)
        p[byte] |= 1 << bit
        k = call(bytes(p))
        diff = [(BASE + i, a, b) for i, (a, b) in enumerate(zip(base, k)) if a != b]
        if diff:
            out[f"{byte}.{bit}"] = [(hex(a), x, y) for a, x, y in diff]
            print(f"payload[{byte}] bit{bit} ->",
                  [(hex(a), hex(x), hex(y)) for a, x, y in diff])
json.dump(out, open(sys.argv[2], "w"))
print("mapped bits:", len(out))
