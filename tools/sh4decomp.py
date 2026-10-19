"""Validate emu/sh4.py against the deck's OWN decompressor (MAIN.img 0x0900).

Runs the real SH-4 routine over a real stream prefix and compares byte for
byte with tools/lzss_boot.py. Any mismatch = core bug. No devices needed.
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from emu.sh4 import SH4
import lzss_boot as L

RING = 0x0FFFD340
IN_BASE = 0x30000000
OUT_BASE = 0x40000000


class Mem:
    def __init__(self, code, src):
        self.code = code          # bytearray @0
        self.ring = bytearray(0x1011)
        self.src = src            # bytearray
        self.dst = bytearray(len(src) * 9 + 64)
        self.scratch = bytearray(0x20000)
        self.high = 0

    def _r(self, a):
        if a < len(self.code):
            return self.code, a
        if RING <= a < RING + len(self.ring):
            return self.ring, a - RING
        if IN_BASE <= a < IN_BASE + len(self.src):
            return self.src, a - IN_BASE
        if OUT_BASE <= a < OUT_BASE + len(self.dst):
            return self.dst, a - OUT_BASE
        if 0x0FFE0000 <= a < 0x0FFE0000 + len(self.scratch):
            return self.scratch, a - 0x0FFE0000
        raise MemoryError(f"{a:#x}")

    def _t(self, a):
        # P1/P2 -> physical (test mirror of sh4.translate for these areas)
        if 0x80000000 <= a < 0xC0000000:
            a &= 0x1FFFFFFF
        return a

    def r8(self, a):
        b, o = self._r(self._t(a))
        return b[o]

    def w8(self, a, v):
        a = self._t(a)
        b, o = self._r(a)
        b[o] = v & 0xFF
        if OUT_BASE <= a:
            self.high = max(self.high, a - OUT_BASE + 1)

    def r16(self, a):
        a = self._t(a)
        b, o = self._r(a)
        return b[o] | (b[o + 1] << 8)

    def w16(self, a, v):
        a = self._t(a)
        b, o = self._r(a)
        b[o] = v & 0xFF
        b[o + 1] = (v >> 8) & 0xFF

    def r32(self, a):
        a = self._t(a)
        b, o = self._r(a)
        return struct.unpack_from("<I", b, o)[0]

    def w32(self, a, v):
        struct.pack_into("<I", self._r(self._t(a))[0], self._r(self._t(a))[1], v & 0xFFFFFFFF)

    def r64(self, a):
        return self.r32(a) | (self.r32(a + 4) << 32)

    def w64(self, a, v):
        self.w32(a, v & 0xFFFFFFFFFFFFFFFF)
        self.w32(a + 4, (v >> 32) & 0xFFFFFFFF)

    def take_interrupt(self):
        return None

    def tick(self, c):
        pass


def main():
    img = open("work/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, 0x10000)[0]
    stream = img[0x10008:0x10008 + ln]
    # prefix sized for a quick but meaningful check (~600 output bytes)
    sub = stream[:220]
    want = L.decode(sub)
    print(f"stream prefix {len(sub)} B -> {len(want)} B expected")

    code = bytearray(img[:0xC40])
    m = Mem(code, bytearray(sub))
    cpu = SH4(m)
    cpu.reset()
    # call convention from tools/sh4sim.py: r4=src, r5=dst, r6=len
    cpu.set_gr(4, IN_BASE)
    cpu.set_gr(5, OUT_BASE)
    cpu.set_gr(6, len(sub))
    cpu.set_gr(15, 0x0FFF0000)
    cpu.pr = 0x0FFE0000  # return address: scratch (zeros -> illegal, stops us)
    cpu.pc = 0x900
    t = time.time()
    # run until PC leaves code or step cap; decompressor returns via rts->pr
    steps = 0
    while steps < 4000000:
        if cpu.pc == 0x0FFE0000:
            break
        cpu.check_interrupt()
        pc = cpu.pc
        try:
            op = cpu._rd(pc, 2, fetch=True)
        except Exception as e:
            print(f"fetch fault at {pc:#x}: {e}")
            return 1
        nxt = (pc + 2) & 0xFFFFFFFF
        try:
            res = cpu.step(op, nxt)
        except Exception as e:
            print(f"TRAP at {pc:#x} op={op:#06x}: {e}")
            return 1
        cpu.steps += 1
        if res is not None:
            tgt, delay, cyc = res
            if delay:
                dop = cpu._rd(nxt, 2, fetch=True)
                cpu.pc = nxt
                dres = cpu.step(dop, (nxt + 2) & 0xFFFFFFFF)
                assert dres is None, "branch in delay slot"
                cpu.steps += 1
            if isinstance(tgt, tuple):
                _, ssr, _ = tgt
                cpu.sr = ssr
                cpu.pc = tgt[0]
            elif tgt == "exc":
                pass
            elif tgt is not None:
                cpu.pc = tgt
            else:
                cpu.pc = nxt
        else:
            cpu.pc = nxt
        steps += 1
    dt = time.time() - t
    got = bytes(m.dst[:m.high])
    print(f"emulated {steps} steps in {dt:.1f}s ({steps / max(dt, 1e-9):,.0f} steps/s)")
    print(f"produced {len(got)} B, watermark match: {len(got) == m.high}")
    if got == want[:len(got)] and len(got) == len(want):
        print(f"DECOMPRESSOR MATCH: {len(got)} bytes identical")
        return 0
    for i, (a, b) in enumerate(zip(got, want)):
        if a != b:
            print(f"first diff at {i}: emu={a:#x} py={b:#x}")
            break
    print(f"lengths: emu {len(got)} vs py {len(want)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
