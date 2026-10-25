#!/usr/bin/env python3
"""keyshift_jump_emu.py -- EMULATOR ONLY: live key shift + beat jumps.

    SEMIS=6 .venv/bin/python tools/keyshift_jump_emu.py [SNAP] [JUMPS]

Tester (2026-10-25): "with Master Tempo on, changing the key freezes the
player and it reports a corrupted file". Plain playback with a shift is
proven (keyshift_dsp_emu.py); a DJ also jumps around. This runs the real
DSP with the key-shift code installed, a shift set, MT on, and fires
JUMPS beat jumps (KEY94 0x04 = Rev1 / KEY93 0x80 = Fwd1 -- the same key
bits the PERFORM pads hold), alternating back/forward, while playing.
CAVEAT (first run 2026-10-25): the direct key-bit writes did NOT make
the deck jump (playhead advanced the same +49 blocks after "rev" and
"fwd" -- the panel parser rewrites the bitmap every frame; the PERFORM
pads re-assert it from the touch hook). So this currently proves ~4 s of
shifted playback with no error only. TODO: drive the jumps by touching
the BEAT JUMP pads (perform_pads installed) instead.
PASS = no DSP error, the stock E-8302 / "collapsed" never shows (red
error text), the audio keeps coming (no long silent run) and the DSP
playhead block keeps moving.
"""
import os
import sys
sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
import keyshift_dsp_emu as K             # noqa: E402

KEY93, KEY94 = 0x0B6C7A85, 0x0B6C7A86
PLAYBLK = 0x11837CCC


def main():
    snap = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_playing7.pkl"
    jumps = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    semis = float(os.environ.get("SEMIS", "6"))
    blob = open(K.BIN, "rb").read()
    m = Machine.load(snap)
    d, mem = m.mem.dev.dspemu, m.mem
    K.mt_on(m)
    if not os.environ.get("NOKS"):
        K.install_dsp(d, blob)
        mem.w16(K.SH_STORE, 0x0009)
        mem.w32(K.SH_SLOT, 0x40000000 | K.q20(2 ** (semis / 12)))
    K.play(m, 0.5)
    words, blocks = [], []
    for j in range(jumps):
        addr, bit = (KEY94, 0x04) if j % 2 == 0 else (KEY93, 0x80)
        b0 = d.bus.read(PLAYBLK, 4)
        for _ in range(6):                       # hold ~ a few panel frames
            mem.w8(addr, mem.r8(addr) | bit)
            m.cpu.run(limit=1_500_000)
        mem.w8(addr, mem.r8(addr) & ~bit)
        words += K.play(m, 0.6)
        b1 = d.bus.read(PLAYBLK, 4)
        blocks.append((b0, b1))
        print(f"jump {j} ({'rev' if j % 2 == 0 else 'fwd'}): play block {b0} -> {b1} "
              f"err={d.error}", flush=True)
    x = np.array([(w >> 16) - (1 << 16 if w >> 31 else 0) for w in words[::2]], np.int32)
    # longest silent run (|s| < 64) in 1/100 s windows
    win = 441
    loud = [np.abs(x[i:i + win]).max() >= 64 for i in range(0, len(x) - win, win)]
    run = best = 0
    for v in loud:
        run = 0 if v else run + 1
        best = max(best, run)
    moved = sum(1 for b0, b1 in blocks if b1 != b0)
    ok = d.error is None and best < 30 and moved == len(blocks)
    print(f"audio {len(x)/44100:.2f}s, longest silence {best/100:.2f}s, "
          f"playhead moved in {moved}/{len(blocks)} -> RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
