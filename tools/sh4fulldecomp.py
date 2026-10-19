"""Full-stream proof: emulated firmware decompressor vs lzss_boot.py over all
of block1 (7MB in). Settles whether core+routine match the reference
end to end. Slow (~10 min); run in background.
Usage: .venv/bin/python tools/sh4fulldecomp.py
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
IN_BASE = 0x81000000
OUT_BASE = 0x82000000
IN_PHYS = 0x01000000
OUT_PHYS = 0x02000000


class Mem:
    def __init__(self, code, src, outcap):
        self.code = code
        self.ring = bytearray(0x1011)
        self.src = src
        self.dst = bytearray(outcap)
        self.scratch = bytearray(0x20000)
        self.high = 0

    def _r(self, a):
        if a < len(self.code):
            return self.code, a
        if RING <= a < RING + len(self.ring):
            return self.ring, a - RING
        if IN_PHYS <= a < IN_PHYS + len(self.src):
            return self.src, a - IN_PHYS
        if OUT_PHYS <= a < OUT_PHYS + len(self.dst):
            return self.dst, a - OUT_PHYS
        if 0x0FFE0000 <= a < 0x0FFE0000 + len(self.scratch):
            return self.scratch, a - 0x0FFE0000
        raise MemoryError(hex(a))

    def _t(self, a):
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
        if OUT_PHYS <= a:
            self.high = max(self.high, a - OUT_PHYS + 1)

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
        a = self._t(a)
        b, o = self._r(a)
        struct.pack_into("<I", b, o, v & 0xFFFFFFFF)

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
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    stream = img[0x40008:0x40008 + ln]
    print(f"stream {len(stream):,} B", flush=True)
    want = L.decode(bytes(stream))
    print(f"reference {len(want):,} B", flush=True)
    code = bytearray(img[:0xC40])
    m = Mem(code, bytes(stream), 64 * 1024 * 1024)
    cpu = SH4(m)
    cpu.reset()
    cpu.set_gr(4, IN_BASE)
    cpu.set_gr(5, OUT_BASE)
    cpu.set_gr(6, len(stream))
    cpu.set_gr(15, 0x0FFF0000)
    cpu.pr = 0x0FFE0000
    cpu.pc = 0x900
    t = time.time()
    steps = 0
    try:
        while steps < 2000000000:
            if cpu.pc == 0x0FFE0000:
                break
            op = cpu._rd(cpu.pc, 2, fetch=True)
            nxt = (cpu.pc + 2) & 0xFFFFFFFF
            res = cpu.step(op, nxt)
            if res is not None:
                tgt, delay, cyc = res
                if delay:
                    dop = cpu._rd(nxt, 2, fetch=True)
                    cpu.pc = nxt
                    dres = cpu.step(dop, (nxt + 2) & 0xFFFFFFFF)
                    assert dres is None, f"branch in delay slot at {nxt:#x}"
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
            if steps % 50000000 == 0:
                print(f"  ...{steps:,} steps, out {m.high:,} B, "
                      f"{steps / max(time.time() - t, 1e-9):,.0f}/s", flush=True)
    except Exception as e:
        print(f"FAULT at pc={cpu.pc:#x} steps={steps}: {e!r}")
        return 1
    dt = time.time() - t
    got = bytes(m.dst[:m.high])
    print(f"done {steps:,} steps {dt:.0f}s, got {len(got):,} want {len(want):,}",
          flush=True)
    n = min(len(got), len(want))
    bad = 0
    first = None
    for i in range(n):
        if got[i] != want[i]:
            bad += 1
            if first is None:
                first = i
    print(f"diffs: {bad} first at: {hex(first) if first is not None else None}",
          flush=True)
    open("/tmp/fulldecomp_got.bin", "wb").write(got)
    return 0 if (bad == 0 and len(got) == len(want)) else 1


if __name__ == "__main__":
    sys.exit(main())
