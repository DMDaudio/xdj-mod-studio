#!/usr/bin/env python3
"""Run the XDJ-1000MK2 loader in the SH-4 emulator and let it unpack MAIN.

The deck unpacks its own firmware: flash S-records -> RAM 0x08000000.
Permissive bus (unknown devices read 0 / ignore writes); trace watches
for the unpacked image forming in RAM, then dumps it for hash check.

Oracle: 29,275,684 B, sha256 46aaaed9...7df.
"""
import sys, struct, hashlib, time
sys.path.insert(0, ".")
from emu.sh4 import SH4, PcHalt

WANT = "46aaaed98179157c523cd2797ecd3223862387a8baec21c9d4dffba87a1237df"
WLEN = 29275684


class Bus:
    def __init__(self, flash):
        self.flash = flash
        self.ram = bytearray(64 * 1024 * 1024)
        self.ram_written = bytearray(64 * 1024 * 1024)  # bitmap-ish (bytes)
        self.ram_count = 0
        self.dev_reads = {}
        self.dev_writes = {}
        self.tick = None

    def _loc(self, a):
        m = a & 0x1FFFFFFF
        if m < len(self.flash):
            return ("flash", m)
        if 0x08000000 <= m < 0x0C000000:
            return ("ram", m - 0x08000000)
        return ("dev", a)

    def r8(self, a):
        k, o = self._loc(a)
        if k == "flash":
            return self.flash[o]
        if k == "ram":
            return self.ram[o]
        self.dev_reads[a & 0xFFFFFFFC] = self.dev_reads.get(a & 0xFFFFFFFC, 0) + 1
        return 0

    def r16(self, a):
        return self.r8(a) | (self.r8(a + 1) << 8)

    def r32(self, a):
        return self.r8(a) | (self.r8(a + 1) << 8) | (self.r8(a + 2) << 16) | (self.r8(a + 3) << 24)

    def r64(self, a):
        return self.r32(a) | (self.r32(a + 4) << 32)

    def w8(self, a, v):
        k, o = self._loc(a)
        if k == "ram":
            if not self.ram_written[o]:
                self.ram_written[o] = 1
                self.ram_count += 1
            self.ram[o] = v & 0xFF
        elif k == "dev":
            self.dev_writes[a & 0xFFFFFFFC] = self.dev_writes.get(a & 0xFFFFFFFC, 0) + 1

    def w16(self, a, v):
        self.w8(a, v); self.w8(a + 1, v >> 8)

    def w32(self, a, v):
        self.w8(a, v); self.w8(a + 1, v >> 8); self.w8(a + 2, v >> 16); self.w8(a + 3, v >> 24)

    def w64(self, a, v):
        self.w32(a, v); self.w32(a + 4, v >> 32)

    def take_interrupt(self, imask):
        return None


def load_flash():
    raw = open("XDJ-1000/XDJ1KMK2.UPD", "rb").read()
    flash = bytearray(b"\xff" * 0xB00000)
    prev = -1
    for line in raw.split(b"\n"):
        if not line.startswith(b"S2"):
            continue
        rec = bytes.fromhex(line[2:].decode("ascii").strip())
        ln = rec[0]
        addr = int.from_bytes(rec[1:4], "big")
        if addr < prev:
            break  # PANL section: separate overlay
        prev = addr
        flash[addr:addr + ln - 4] = rec[4:ln - 1]
    return bytes(flash)


def main():
    t0 = time.time()
    bus = Bus(load_flash())
    c = SH4(bus)
    c.reset()
    print(f"reset pc={c.pc:#x}")
    stop_ram = {"n": 0}
    fixed = {"odd": 0, "delay": 0}

    def tr(pc, op):
        # hw quirk: boot jumps to odd 0xA000007A (lands mid-function 0x7A)
        if pc & 1:
            c.pc &= ~1
            fixed["odd"] += 1
            return
        # dt-r1 delay loop at 0x134 spins ~4B times on garbage r1: shorten
        if (pc & 0x1FFFFFFF) == 0x134 and fixed["delay"] < 5:
            try:
                c.b1[1] = 1
            except Exception:
                pass
            fixed["delay"] += 1
        m = pc & 0x1FFFFFFF
        if 0x08000000 <= m < 0x0C000000:
            stop_ram["n"] += 1
            if stop_ram["n"] > 1000:
                raise PcHalt(pc)
    c.trace = tr
    total = 0
    try:
        for chunk in range(40):
            c.run(limit=5000000)
            total += 5000000
            el = time.time() - t0
            print(f"steps={total} pc={c.pc:#x} ram_written={bus.ram_count} "
                  f"({bus.ram_count / 2**20:.1f} MB) t={el:.0f}s", flush=True)
            if bus.ram_count >= WLEN:
                break
    except PcHalt as h:
        print("JUMPED TO RAM at", hex(h.pc))
    c.trace = None
    # dump + hash the RAM image
    out = bytes(bus.ram[:WLEN])
    oh = hashlib.sha256(out).hexdigest()
    print("ram dump sha:", oh)
    if oh == WANT:
        open("XDJ-1000/MAIN1000.img", "wb").write(out)
        print("MATCH - wrote XDJ-1000/MAIN1000.img")
        return 0
    # nearest-prefix info for diagnosis
    print("top device reads:", sorted(bus.dev_reads.items(), key=lambda kv: -kv[1])[:8])
    print("top device writes:", sorted(bus.dev_writes.items(), key=lambda kv: -kv[1])[:8])
    return 1


if __name__ == "__main__":
    sys.exit(main())
