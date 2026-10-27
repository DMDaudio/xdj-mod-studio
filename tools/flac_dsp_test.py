#!/usr/bin/env python3
"""flac_dsp_test.py -- the FLAC load on the REAL-audio-chip emulator.

Deck 2026-10-25: browse to a .flac, push = load -> the deck froze. The
older vfile_emu_test ran on the stand-in audio chip (loads always ended in
E-8302 there), so a load never got far enough to test. Here:
  1. tools/vfile_emu_test.py with CKPT=cache/emu_dsp_mounted6.pkl and no
     HLE: install the layer, mount /tmp/flacstick, cursor on the FLAC,
     save cache/vfile_on_flac.pkl  (run that first)
  2. this: load it, run ~10 emulated s in slices, and report per slice the
     transport mode, the DSP's state/error, CPU exceptions, whether the
     screen still changes, the layer's counters and where the CPU spends
     its time (a freeze shows as a frozen screen / one hot loop).
"""
import collections
import hashlib
import os
import sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import vfile_emu_test as V               # noqa: E402


def fbhash(m):
    p = "/tmp/flac_dsp_fb.png"
    fb_png(m, p)
    return hashlib.md5(open(p, "rb").read()).hexdigest()[:8]


def main():
    s = V.syms()
    m = Machine.load(sys.argv[1] if len(sys.argv) > 1 else "cache/vfile_on_flac.pkl")
    mem = m.mem
    d = getattr(mem.dev, "dspemu", None)
    st = lambda: [mem.r32(s["vf_stats"] + 4 * i) for i in range(7)]
    print("before load: stats", st(), "dsp", d is not None)
    V.key(m, "RotaryPush", 5_000_000)
    e0 = m.exc_total
    seen = []
    for k in range(12):
        pcs = collections.Counter()
        for _ in range(10):
            m.cpu.run(limit=10_000_000)
            pcs[m.cpu.pc & ~0xFF] += 1
        h = fbhash(m)
        seen.append(h)
        bf8 = d.bus.read(0x11837BF8, 4) if d else None
        print(f"slice {k}: mode {mem.r32(0x0B6C4264)} dsp state {bf8} err "
              f"{getattr(d, 'error', None)} exc +{m.exc_total - e0} screen {h} "
              f"stats {st()} hot {[hex(p) for p, _ in pcs.most_common(3)]}", flush=True)
    if os.environ.get("SEEK"):
        # 2026-10-25 deck: "tried to skip through -> stopped working,
        # sometimes emergency loop". Hold SEARCH >> (fast scan) then jump
        # with TRACK SEARCH-style needle moves: watch the layer's reads.
        from emu import panl
        for rep in range(3):
            mem.dev.panel_frame = panl.build_frame(keys=["ScanFwd"])
            for _ in range(8):
                m.cpu.run(limit=10_000_000)
            mem.dev.panel_frame = panl.build_frame()
            for k in range(4):
                pcs = collections.Counter()
                for _ in range(10):
                    m.cpu.run(limit=10_000_000)
                    pcs[m.cpu.pc & ~0xFF] += 1
                h = fbhash(m)
                seen.append(h)
                bf8 = d.bus.read(0x11837BF8, 4) if d else None
                print(f"after scan {rep}.{k}: mode {mem.r32(0x0B6C4264)} dsp state {bf8} "
                      f"err {getattr(d, 'error', None)} exc +{m.exc_total - e0} screen {h} "
                      f"stats {st()} hot {[hex(p) for p, _ in pcs.most_common(2)]}", flush=True)
    fb_png(m, "screenshots/flac_dsp_after.png")
    frozen = len(set(seen[-6:])) == 1
    print("RESULT:", "FROZE (screen stopped changing)" if frozen else "screen alive",
          "| exceptions", m.exc_total - e0)


if __name__ == "__main__":
    main()
