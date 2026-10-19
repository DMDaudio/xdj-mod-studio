#!/usr/bin/env python3
"""warmpipe_test.py -- EMULATOR ONLY.

The pipe-warmth theory (2026-10-18): the masked cave boot passes on the
COLD-pipe emu snapshot but failed on the deck, whose pipe was warm from
earlier playback. This script builds the deck-alike state and re-runs the
masked cave attempt on it:

  1. load WARM_SRC (default cache/emu_track_loaded.pkl: a real track-load
     through Browse has exercised the pipe: decode buffers + mailbox rings
     written by the resident loader tasks)
  2. press PlayPause to stop; run to settle (~50M)
  3. zero TRACK_LEN (0x0BA3C050) to mimic the deck's ejected state
     (otherwise the cave's gate refuses with status 2)
  4. save OUT (cache/emu_warmpipe_idle.pkl)
  5. install the v6 cave exactly like keyshift_reload_emu (shell out to it
     with OUT as the snapshot) and report the result lines.

If the masked attempt wedges (status stays 0 / boot_ret != 0) on the warm
pipe while it passed on the cold pipe, the pipe-warmth theory is CONFIRMED
and the cave fix = quiesce the pipe before the boot call.
"""
import os
import sys
import time
import subprocess
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

SRC = os.environ.get("WARM_SRC", "cache/emu_track_loaded.pkl")
OUT = os.environ.get("WARM_OUT", "cache/emu_warmpipe_idle.pkl")
TRACK_LEN = 0x0BA3C050


def build():
    t0 = time.time()
    m = Machine.load(SRC)
    mem = m.mem
    print(f"loaded {SRC}: steps {m.cpu.steps:,}", flush=True)
    dev = mem.dev
    tl = mem.r32(TRACK_LEN)
    print(f"TRACK_LEN pre = {tl:#x}", flush=True)
    m.cpu.run(limit=20_000_000)          # settle
    dev.panel_frame = panl.build_frame(["PlayPause"])
    m.cpu.run(limit=30_000_000)
    dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=50_000_000)
    print("PlayPause pressed; settled", flush=True)
    mem.w32(TRACK_LEN, 0)                # mimic the deck's no-track state
    print(f"TRACK_LEN zeroed; saving {OUT} [{time.time()-t0:.0f}s]", flush=True)
    m.save(OUT)


def run_cave():
    env = dict(os.environ, OUT="cache/emu_ks_warmpipe.pkl")
    r = subprocess.run([sys.executable, "tools/keyshift_reload_emu.py", OUT],
                       capture_output=True, text=True, env=env)
    print(r.stdout[-2500:])
    if r.stderr:
        print("stderr:", r.stderr[-800:])


if __name__ == "__main__":
    if "skipbuild" not in sys.argv:
        build()
    run_cave()
