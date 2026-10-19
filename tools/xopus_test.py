#!/usr/bin/env python3
"""xopus_test.py [SECONDS] -- the deck Opus decoder must equal the Mac one.

1. ffmpeg encodes SECONDS (default 4) of a real track to Ogg Opus 48 kHz
   stereo, the packets are pulled out of the Ogg pages here.
2. build/xopus/xopus_native decodes them (Mac, fixed-point libopus).
3. build/xopus/xopus_sh.bin (linked at 0x0F000000; XO_BASE) runs INSIDE
   the emulated XDJ-700 (cache/emu_track_loaded.pkl, C core): every
   packet is a real call into xo_decode with interrupts blocked, the CPU
   state restored afterwards.
PASS = PCM bit-identical. Also prints SH instructions per second of
audio (deck CPU budget estimate; SH-4A @ ~500 MHz).
Rebuild first: sh tools/xopus/build.sh
"""
import os
import struct
import subprocess
import sys
import tempfile
sys.path.insert(0, ".")

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
BASE = 0x0F000000
STATE, PKT, PCM, STACK = 0x0F100000, 0x0F110000, 0x0F120000, 0x0F1FFF00
HALT = 0x0F1FFF80                 # bra $ ; nop  -> call returned
SRC = os.path.expanduser("~/Downloads/Rekordbox stuff/Contents/Lifelike & "
                         "Roman Salzger/UnknownAlbum/Lifelike & Roman "
                         "Salzger - Radio 84.aiff")


def ogg_packets(data):
    """Opus packets from an Ogg stream (skips OpusHead/OpusTags)."""
    pkts, cur, i = [], b"", 0
    while i < len(data):
        assert data[i:i + 4] == b"OggS", "bad ogg page"
        nseg = data[i + 26]
        segs = data[i + 27:i + 27 + nseg]
        j = i + 27 + nseg
        for s in segs:
            cur += data[j:j + s]
            j += s
            if s < 255:
                pkts.append(cur)
                cur = b""
        i = j
    return [p for p in pkts if not p.startswith((b"OpusHead", b"OpusTags"))]


def main():
    tmp = tempfile.mkdtemp()
    ogg = os.path.join(tmp, "t.opus")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "60", "-t", str(SECS),
                    "-i", SRC, "-ar", "48000", "-ac", "2", "-c:a", "libopus",
                    "-b:a", "128k", ogg], check=True)
    pkts = ogg_packets(open(ogg, "rb").read())
    pf = os.path.join(tmp, "p.bin")
    with open(pf, "wb") as f:
        for p in pkts:
            f.write(struct.pack("<I", len(p)) + p)
    ref = subprocess.run(["build/xopus/xopus_native", pf, "2"],
                         capture_output=True, check=True).stdout
    print(f"{len(pkts)} packets, native PCM {len(ref)} bytes")

    from emu.machine import Machine
    m = Machine.load("cache/emu_track_loaded.pkl")
    mem, c = m.mem, m.cpu
    img = open("build/xopus/xopus_sh.bin", "rb").read()
    for i, b in enumerate(img):
        mem.w8(BASE + i, b)
    mem.w16(HALT, 0xAFFE)          # bra $
    mem.w16(HALT + 2, 0x0009)      # nop
    entry = [mem.r32(BASE + 4 * k) for k in range(3)]
    print("entry", [hex(e) for e in entry])
    regs0 = [c.gr(i) for i in range(16)]
    pc0, pr0, sr0, sl0 = c.pc, c.pr, c.sr, c.sleeping

    def call(fn, *args):
        for i, v in enumerate(args):
            c.set_gr(4 + i, v)
        c.set_gr(15, STACK)
        c.pr, c.pc = HALT, fn
        c.sleeping = 0                       # snapshot idles in SLEEP
        c.sr = sr0 | 0x10000000 | 0xF0      # BL=1, IMASK=15: no interrupts
        n = 0
        while c.pc != HALT:
            c.run(limit=20_000)
            n += 1
            if n > 500000:
                raise RuntimeError(f"stuck at {c.pc:#x}")
        return c.gr(0) - (1 << 32) if c.gr(0) & 0x80000000 else c.gr(0)

    size = call(entry[0], 2)
    rc = call(entry[1], STATE, 2)
    print("state size", size, "init", rc)
    out, steps0 = bytearray(), c.steps
    for p in pkts:
        for i, b in enumerate(p):
            mem.w8(PKT + i, b)
        n = call(entry[2], STATE, PKT, len(p), PCM)
        if n < 0:
            print("decode error", n)
            break
        out += bytes(mem.r8(PCM + i) for i in range(n * 4))
        if len(out) // 4 % (48000) < n:
            print(f"  {len(out) / 192000:.1f} s decoded, {c.steps - steps0:,} insns", flush=True)
    insns = c.steps - steps0
    for i, v in enumerate(regs0):          # put the deck back as it was
        c.set_gr(i, v)
    c.pc, c.pr, c.sr, c.sleeping = pc0, pr0, sr0, sl0
    secs = len(out) / 4 / 48000
    same = bytes(out) == ref
    diff = next((i for i in range(min(len(out), len(ref))) if out[i] != ref[i]), None)
    print(f"SH PCM {len(out)} bytes, {secs:.2f} s; first diff at {diff}")
    print(f"SH instructions: {insns:,} = {insns / max(secs, 1e-9) / 1e6:.1f} M per second "
          f"of stereo audio (~{insns / max(secs, 1e-9) / 5e6:.0f}% of a 500 MHz SH-4A at 1 IPC)")
    print("RESULT:", "PASS (bit-identical)" if same else "FAIL")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
