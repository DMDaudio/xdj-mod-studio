#!/usr/bin/env python3
"""xflac_test.py [SECONDS] -- the deck FLAC decoder must be lossless.

ffmpeg makes a 16-bit stereo FLAC of a real track (SECONDS, default 6).
Reference = ffmpeg's own decode (lossless). build/xflac/xflac_native and
build/xflac/xflac_sh.bin (run INSIDE the emulated XDJ-700, C core, calls
with interrupts blocked, CPU state restored) must both equal it bit for
bit, from the start AND after a seek into the middle. Also a 24-bit file:
native vs deck build equal. Prints deck instructions per audio second.
Rebuild first: sh tools/xflac/build.sh
"""
import os
import subprocess
import sys
import tempfile
sys.path.insert(0, ".")

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
BASE = 0x0F000000
DATA, ARENA, PCM, STACK = 0x0F100000, 0x0F600000, 0x0F800000, 0x0F1FFF00 + 0x7F0000
HALT = 0x0F8FFF80
SRC = os.path.expanduser("~/Downloads/Rekordbox stuff/Contents/Lifelike & "
                         "Roman Salzger/UnknownAlbum/Lifelike & Roman "
                         "Salzger - Radio 84.aiff")
CHUNK = 4096


def ff(args):
    return subprocess.run(["ffmpeg", "-v", "error", "-y"] + args,
                          capture_output=True, check=True).stdout


def main():
    tmp = tempfile.mkdtemp()
    f16, f24 = os.path.join(tmp, "t16.flac"), os.path.join(tmp, "t24.flac")
    ff(["-ss", "60", "-t", str(SECS), "-i", SRC, "-ac", "2", "-sample_fmt", "s16", f16])
    ff(["-ss", "60", "-t", "2", "-i", SRC, "-ac", "2", "-sample_fmt", "s32",
        "-bits_per_raw_sample", "24", f24])
    ref = ff(["-i", f16, "-f", "s16le", "-"])
    nat = subprocess.run(["build/xflac/xflac_native", f16], capture_output=True,
                         check=True).stdout
    seek_at = 100000
    nat_seek = subprocess.run(["build/xflac/xflac_native", f16, str(seek_at)],
                              capture_output=True, check=True).stdout
    nat24 = subprocess.run(["build/xflac/xflac_native", f24], capture_output=True,
                           check=True).stdout
    print(f"ffmpeg ref {len(ref)} B, native {len(nat)} B, native==ffmpeg: {nat == ref}")

    from emu.machine import Machine
    m = Machine.load("cache/emu_track_loaded.pkl")
    mem, c = m.mem, m.cpu
    img = open("build/xflac/xflac_sh.bin", "rb").read()
    for i, b in enumerate(img):
        mem.w8(BASE + i, b)
    mem.w16(HALT, 0xAFFE)
    mem.w16(HALT + 2, 0x0009)
    ent = [mem.r32(BASE + 4 * k) for k in range(4)]
    regs0 = [c.gr(i) for i in range(16)]
    st0 = (c.pc, c.pr, c.sr, c.sleeping)

    def call(fn, *args):
        for i, v in enumerate(args):
            c.set_gr(4 + i, v & 0xFFFFFFFF)
        c.set_gr(15, STACK)
        c.pr, c.pc, c.sleeping = HALT, fn, 0
        c.sr = st0[2] | 0x100000F0
        n = 0
        while c.pc != HALT:
            c.run(limit=20_000)
            n += 1
            if n > 2_000_000:
                raise RuntimeError(f"stuck at {c.pc:#x}")
        v = c.gr(0)
        return v - (1 << 32) if v & 0x80000000 else v

    def load(path):
        d = open(path, "rb").read()
        for i, b in enumerate(d):
            mem.w8(DATA + i, b)
        return len(d)

    def decode(path, seek=None):
        n = load(path)
        h = call(ent[0], ARENA, 0x200000, DATA, n)
        if not h:
            raise RuntimeError("open failed")
        ch = call(ent[1], h, 1)
        if seek is not None:
            assert call(ent[3], h, seek) == 1
        out, s0 = bytearray(), c.steps
        while True:
            k = call(ent[2], h, PCM, CHUNK)
            if k <= 0:
                break
            out += bytes(mem.ram1[PCM - 0x0C000000:PCM - 0x0C000000 + k * ch * 2]) \
                if hasattr(mem, "ram1") else bytes(mem.r8(PCM + i) for i in range(k * ch * 2))
        info = [call(ent[1], h, w) for w in range(4)]
        return bytes(out), c.steps - s0, info

    sh, insns, info = decode(f16)
    secs = len(sh) / 4 / info[0]
    print(f"deck 16-bit: {len(sh)} B, rate {info[0]} ch {info[1]} bits {info[2]}, "
          f"==ffmpeg: {sh == ref}, ==native: {sh == nat}")
    print(f"deck cost: {insns / secs / 1e6:.1f} M instructions per audio second "
          f"(~{insns / secs / 5e6:.0f}% of a 500 MHz SH-4A)")
    shs, _, _ = decode(f16, seek_at)
    print(f"deck seek to frame {seek_at}: ==native seek: {shs == nat_seek}, "
          f"==ffmpeg tail: {shs == ref[seek_at * 4:]}")
    sh24, _, info24 = decode(f24)
    print(f"deck 24-bit (bits {info24[2]}): ==native: {sh24 == nat24}")
    for i, v in enumerate(regs0):
        c.set_gr(i, v)
    c.pc, c.pr, c.sr, c.sleeping = st0
    ok = (nat == ref and sh == ref and shs == nat_seek and shs == ref[seek_at * 4:]
          and sh24 == nat24)
    print("RESULT:", "PASS (lossless, bit-identical)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
