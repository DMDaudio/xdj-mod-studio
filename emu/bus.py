"""Memory bus for the XDJ-700 emulator.

Documented map (from repo RE notes):
  MAIN flash  7,251,904 B  phys 0x00000000, P2 alias 0xA0000000
  SDRAM       64 MB DDR2    phys 0x08000000-0x0BFFFFFF, P2 0xA8000000
  Framebuffer 800x480 RGB565 double-buffered:
      buffer 0 @ 0x0E780000, buffer 1 @ 0x0E83B800 (768_000 B each)
  Stack page  around 0x0FFF0000 / init SP 0xB0000000
  MMIO (stubbed, log-and-ignore):
      CCR 0xFF00001C, SCIF0 0xFFE00000, SCIF1 0xFFE10000,
      PFC 0xA4050100..., BSC/SDRAM 0xFD000008..0xFD000060,
      LCDC/other SH7724 regs, DSP CS6 0x18040000/0x180C0000.

P2 aliasing: bit 29 set (0x20000000) maps to physical for the low regions,
i.e. phys = addr & ~0x20000000 for addr in P2 views. Implemented generally
via _phys().
"""
import struct

FB_W, FB_H = 800, 480
FB_SIZE = FB_W * FB_H * 2
FB0 = 0x0E780000
FB1 = 0x0E83B800

SDRAM_BASE = 0x08000000
SDRAM_SIZE = 64 * 1024 * 1024

MMIO_RANGES = [
    (0xFF000000, 0x1000000, "SH4 on-chip regs (CCR etc)"),
    (0xFFE00000, 0x20000, "SCIF0/1"),
    (0xA4050000, 0x10000, "PFC"),
    (0xFD000000, 0x100000, "BSC/SDRAM ctl"),
    (0xFE000000, 0x200000, "LCDC/other"),
    (0x18000000, 0x100000, "DSP CS6"),
    (0xB8000000, 0x100000, "DSP CS6 P2"),
    (0x0FFFD000, 0x10000, "resident unpacker window"),
]


def _phys(addr):
    # Explicit P1/P2 aliases for memory only. Peripheral addresses that
    # happen to live in the P2 window (PFC 0xA405xxxx, etc.) must NOT be
    # masked -- they are MMIO, not RAM aliases.
    if 0xA0000000 <= addr < 0xA0800000:  # flash, P2 uncached
        return addr - 0xA0000000
    if 0x80000000 <= addr < 0x80800000:  # flash, P1 cached
        return addr - 0x80000000
    if 0xA8000000 <= addr < 0xAC000000:  # SDRAM, P2
        return (addr - 0xA8000000) + SDRAM_BASE
    if 0x88000000 <= addr < 0x8C000000:  # SDRAM, P1
        return (addr - 0x88000000) + SDRAM_BASE
    return addr


class Bus:
    def __init__(self, flash=None, log_mmio=False):
        self.flash = bytearray(flash) if flash is not None else bytearray()
        self.ram = bytearray(SDRAM_SIZE)
        self.fb0 = bytearray(FB_SIZE)
        self.fb1 = bytearray(FB_SIZE)
        self.scratch = bytearray(0x20000)  # stack/scratch @ 0x0FFF0000
        self.mmio = {}  # addr -> last written value (byte granularity sparse)
        self.log_mmio = log_mmio
        self.mmio_log = []  # (op, addr, val)
        self.fb_writes = 0

    # -- helpers ---------------------------------------------------------
    def _mmio_range(self, p):
        for base, size, _ in MMIO_RANGES:
            if base <= p < base + size:
                return True
        # stack window
        if 0x0FFE0000 <= p < 0x10000000:
            return False
        if 0xB0000000 <= p < 0xB0010000:
            return False
        return p >= 0x10000000 and (p < SDRAM_BASE or p >= 0x0C000000) and not (
            FB0 <= p < FB0 + FB_SIZE or FB1 <= p < FB1 + FB_SIZE)

    # -- byte access ------------------------------------------------------
    def r8(self, addr):
        p = _phys(addr)
        if p < len(self.flash):
            return self.flash[p]
        if SDRAM_BASE <= p < SDRAM_BASE + SDRAM_SIZE:
            return self.ram[p - SDRAM_BASE]
        if FB0 <= p < FB0 + FB_SIZE:
            return self.fb0[p - FB0]
        if FB1 <= p < FB1 + FB_SIZE:
            return self.fb1[p - FB1]
        if 0x0FFE0000 <= p < 0x10000000:
            return self.scratch[p - 0x0FFE0000]
        if 0xB0000000 <= p < 0xB0010000:
            return self.scratch[p - 0xB0000000]
        if 0x0FFFD000 <= p < 0x10000000:
            return 0
        # MMIO / unmapped -> return 0, optionally log
        if self.log_mmio:
            self.mmio_log.append(("r8", addr, 0))
        return self.mmio.get(p, 0)

    def w8(self, addr, v):
        v &= 0xFF
        p = _phys(addr)
        if p < len(self.flash):
            return  # flash is ROM
        if SDRAM_BASE <= p < SDRAM_BASE + SDRAM_SIZE:
            self.ram[p - SDRAM_BASE] = v
            return
        if FB0 <= p < FB0 + FB_SIZE:
            self.fb0[p - FB0] = v
            self.fb_writes += 1
            return
        if FB1 <= p < FB1 + FB_SIZE:
            self.fb1[p - FB1] = v
            self.fb_writes += 1
            return
        if 0x0FFE0000 <= p < 0x10000000:
            self.scratch[p - 0x0FFE0000] = v
            return
        if 0xB0000000 <= p < 0xB0010000:
            self.scratch[p - 0xB0000000] = v
            return
        self.mmio[p] = v
        if self.log_mmio:
            self.mmio_log.append(("w8", addr, v))

    def r32(self, addr):
        a = addr & ~3
        return (self.r8(a) | (self.r8(a + 1) << 8) |
                (self.r8(a + 2) << 16) | (self.r8(a + 3) << 24)) & 0xFFFFFFFF

    def w32(self, addr, v):
        a = addr & ~3
        for i in range(4):
            self.w8(a + i, (v >> (8 * i)) & 0xFF)

    def r16(self, addr):
        a = addr & ~1
        return (self.r8(a) | (self.r8(a + 1) << 8)) & 0xFFFF

    def w16(self, addr, v):
        a = addr & ~1
        self.w8(a, v & 0xFF)
        self.w8(a + 1, (v >> 8) & 0xFF)

    # -- framebuffer ------------------------------------------------------
    def read_fb(self, which=0):
        """Return raw RGB565 little-endian bytes of buffer 0/1."""
        return bytes(self.fb0) if which == 0 else bytes(self.fb1)

    def active_fb(self):
        # Heuristic: buffer with more non-zero data is "active"
        n0 = sum(1 for b in self.fb0[::4096] if b)
        n1 = sum(1 for b in self.fb1[::4096] if b)
        return 0 if n0 >= n1 else 1
