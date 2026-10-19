"""bus.py -- XDJ-700 DSP memory map (TI OMAP-L1x / C6747 family).

RAM regions (flat bytearrays):
  L2       0x11800000 256 KB (local alias 0x00800000)
  L1P      0x11E00000 32 KB  (alias 0x00E00000)  -- cache/RAM, kept as RAM
  L1D      0x11F00000 32 KB  (alias 0x00F00000)
  shared   0x80000000 128 KB
  DDR/SDR  0xC0000000 64 MB  (EMIFB SDRAM)
Everything else is MMIO: handled by `devices` ({base: obj with read/
write(off, size[, val])}) or logged in `unknown` and read as 0.
"""
import collections

REGIONS = [  # base, size, aliases
    (0x11800000, 0x40000, (0x00800000,)),
    (0x11E00000, 0x8000, (0x00E00000,)),
    (0x11F00000, 0x8000, (0x00F00000,)),
    (0x80000000, 0x20000, ()),
    (0xC0000000, 0x4000000, ()),
]


class DspBus:
    def __init__(self):
        self.ram = []
        for base, size, aliases in REGIONS:
            buf = bytearray(size)
            for b in (base,) + aliases:
                self.ram.append((b, b + size, buf))
        self.devices = {}          # base -> (size, device)
        self.unknown = collections.Counter()
        self.mmio_log = collections.deque(maxlen=4096)
        self.watch = None          # callable(kind, addr, size, val) or None

    def _ram(self, a):
        for lo, hi, buf in self.ram:
            if lo <= a < hi:
                return buf, a - lo
        return None, 0

    def _dev(self, a):
        for base, (size, dev) in self.devices.items():
            if base <= a < base + size:
                return dev, a - base
        return None, 0

    def read(self, a, n):
        buf, o = self._ram(a)
        if buf is not None:
            v = int.from_bytes(buf[o:o + n], "little")
        else:
            dev, off = self._dev(a)
            if dev is not None:
                v = dev.read(off, n)
            else:
                self.unknown[("r", a & ~3)] += 1
                v = 0
            self.mmio_log.append(("r", a, n, v))
        if self.watch:
            self.watch("r", a, n, v)
        return v

    def write(self, a, n, v):
        v &= (1 << (8 * n)) - 1
        if self.watch:
            self.watch("w", a, n, v)
        buf, o = self._ram(a)
        if buf is not None:
            buf[o:o + n] = v.to_bytes(n, "little")
            return
        dev, off = self._dev(a)
        self.mmio_log.append(("w", a, n, v))
        if dev is not None:
            dev.write(off, n, v)
        else:
            self.unknown[("w", a & ~3)] += 1

    def load(self, a, data):
        buf, o = self._ram(a)
        assert buf is not None and o + len(data) <= len(buf), hex(a)
        buf[o:o + len(data)] = data

    def dump(self, a, n):
        buf, o = self._ram(a)
        return bytes(buf[o:o + n])
