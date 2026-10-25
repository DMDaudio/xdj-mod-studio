#!/usr/bin/env python3
"""keyshift_dsp_emu.py -- EMULATOR ONLY: real-time key shift inside the real
DSP program (tools/keyshift_dsp/ks.c), measured on the DSP's audio output.

    SEMIS=2 .venv/bin/python tools/keyshift_dsp_emu.py [SNAP]

Starts from a snapshot with the real DSP playing (default
cache/emu_dsp_playing7.pkl), turns MASTER TEMPO on, then
  CONTROL run : MT on, no shift            -> build/ks_ctl.wav
  SHIFT run   : ks.bin in DSP DDR at 0xC005F4C0, call at 0xC0008D64
                re-pointed, SH tempo-copy store 0x08D36B3A NOPed and
                0x09D24DD8 = 0x40000000|Q20(2^(SEMIS/12)) (-> mailbox BD8)
                                          -> build/ks_shift.wav
and prints pitch (semitones) + tempo ratio of SHIFT vs CONTROL.
PASS = pitch within 0.25 st of SEMIS and tempo within 2 %.
(Patching DDR directly here; on the deck the code arrives through a DSP
reload from a patched program copy -- see keyshift_dsp_reload_emu.py.)
"""
import os
import struct
import sys
import time
sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
import dsp_pitch_probe as P              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "keyshift_dsp", "ks.bin")
KS_BASE = 0xC005F4C0
CALL = 0xC0008D64            # b .S1 0xC001CBF4 (block puller)
CALL_FP = 0xC0008D60         # its fetch packet
PULLER = 0xC001CBF4
SH_STORE = 0x08D36B3A        # mov.l r1,@(20,r12): tempo copy -> 0x09D24DD8
SH_SLOT = 0x09D24DD8


def branch_word(old, fp, target):
    return (old & ~(0x1FFFFF << 7)) | ((((target - fp) >> 2) & 0x1FFFFF) << 7)


def q20(x):
    return int(round(x * (1 << 20))) & 0xFFFFFFFF


def ks_symbol():
    import subprocess
    nm = os.path.join(HERE, "..", "work", "tic6x-tools", "bin", "tic6x-elf-nm")
    out = subprocess.run([nm, os.path.join(HERE, "keyshift_dsp", "ks.elf")],
                         capture_output=True, text=True).stdout
    return {l.split()[2]: int(l.split()[0], 16) for l in out.splitlines()}


def install_dsp(d, blob):
    ks = ks_symbol()["KS"]
    cur = bytes(d.bus.read(KS_BASE + i, 1) for i in range(len(blob)))
    if cur == blob and d.bus.read(CALL, 4) == branch_word(d.bus.read(CALL, 4), CALL_FP, KS_BASE):
        print("  DSP already runs the key-shift code (reloaded) - not patching", flush=True)
        return ks
    for i in range(0, len(blob), 4):
        d._store(KS_BASE + i, int.from_bytes(blob[i:i + 4].ljust(4, b"\0"), "little"))
    for a in range(ks, ks + 0x140, 4):
        d._store(a, 0)
    # v2 (2026-10-18): the stretcher's staging loads -> KS.pbuf (= KS)
    for a, o in ((0xC001CD40, 0x3356B828), (0xC001CD98, 0x0308C168),
                 (0xC001CE34, 0x01D6B828), (0xC001CE48, 0x0188C168)):
        cur = d.bus.read(a, 4)
        c = (ks >> 16) if a in (0xC001CD98, 0xC001CE48) else (ks & 0xFFFF)
        new = (o & ~(0xFFFF << 7)) | (c << 7)
        assert cur in (o, new), (hex(a), hex(cur))
        d._store(a, new)
    old = d.bus.read(CALL, 4)
    assert branch_word(old, CALL_FP, PULLER) == old, hex(old)
    d._store(CALL, branch_word(old, CALL_FP, KS_BASE))
    return ks


def play(m, secs, chunk=100_000_000):
    d = m.mem.dev.dspemu
    n0 = len(d.asp1.out)
    steps = int(secs * P.SH_STEPS_PER_S)
    while steps > 0:
        k = min(steps, chunk)
        m.cpu.run(limit=k)
        steps -= k
    return d.asp1.out[n0:]


def mt_on(m):
    dev = m.mem.dev
    dev.panel_frame = panl.build_frame(keys=["MasterTempo"])
    m.cpu.run(limit=5_000_000)
    dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=30_000_000)


def main():
    snap = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_playing7.pkl"
    semis = float(os.environ.get("SEMIS", "2"))
    secs = float(os.environ.get("SECS", "4"))
    blob = open(BIN, "rb").read()
    res = {}
    for mode in ("ctl", "shift"):
        t = time.time()
        m = Machine.load(snap)
        d, mem = m.mem.dev.dspemu, m.mem
        mt_on(m)
        print(mode, "MT", mem.r8(0x0B6C4308), "BC4", hex(d.bus.read(0x11837BC4, 4)), flush=True)
        ks = None
        if mode == "shift":
            ks = None if os.environ.get("NOKS") else install_dsp(d, blob)
            mem.w16(SH_STORE, 0x0009)
            mem.w32(SH_SLOT, 0x40000000 | q20(2 ** (semis / 12)))
        play(m, 0.3)                                   # settle
        p0 = P.remain_ms(m)
        words = play(m, secs)
        p1 = P.remain_ms(m)
        x = P.wav_write(f"build/ks_{mode}.wav", words)
        res[mode] = x
        extra = ""
        if ks:
            v = [d.bus.read(ks + 256 + 4 * i, 4) for i in range(8)]
            extra = (f" KS magic {v[0]:#x} calls {v[1]} shifted {v[2]} w {v[3]:#x} "
                     f"k {struct.unpack('<f', struct.pack('<I', v[4]))[0]:.4f} "
                     f"nx/ny {v[6]}/{v[7]}")
        print(f"{mode}: BD8 {d.bus.read(0x11837BD8, 4):#x} out {len(x)/P.FS:.2f}s "
              f"pos {(p1-p0)/75:.2f}s err={d.error}{extra} [{time.time()-t:.0f}s]", flush=True)
    ps = P.pitch_shift(res["shift"], res["ctl"])
    tr = P.beat_period(res["ctl"]) / P.beat_period(res["shift"])
    ok = abs(ps - semis) <= 0.25 and abs(tr - 1) <= 0.02
    print(f"PITCH {ps:+.2f} st (want {semis:+.2f}), TEMPO x{tr:.3f} -> RESULT: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
