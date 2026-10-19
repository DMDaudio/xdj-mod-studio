#!/usr/bin/env python3
"""usdb_burst_check.py -- USB-load end-to-end timing through the SHIPPED app.

Builds EmuApp(headless=True) like the bundled Mod Studio, inserts the
user's folder through the real insert path, and drives the deck exactly
like the hosted app does (a 60 Hz tick loop -> inject_panl) inside the
app's own _emu_loop, timing each stage: mount, Browse navigation, track
load, PLAY. Headless only; the source folder is never written to.

Run 1 (default)        : burst re-entry fix active.
Run 2 (XDJ_NO_REBURST) : fix disabled -> shows deck-speed loading.
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.ui import EmuApp

FOLDER = os.environ.get("XDJ_USB_FOLDER",
                        os.path.expanduser("~/Downloads/Rekordbox stuff"))
CKPT = os.environ.get(
    "XDJ_CKPT",
    os.path.expanduser("~/Library/Application Support/XDJ Mod Studio/"
                       "firmware/dspsound.pkl.gz"))


def ops(m):
    d = getattr(m.mem.dev, "usb_disk", None)
    return getattr(d, "ops", 0) if d else 0


def in_burst(app, m):
    u = app.burst_until
    return u is not None and m.cpu.steps < u


def tap(app, key, hold=0.35):
    app.st.held.add(key)
    time.sleep(hold)
    app.st.held.discard(key)


class Host(threading.Thread):
    """A 60 Hz hosted tick loop, no window (calls app.tick like emuhost's
    render thread does)."""
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app, self.alive = app, True

    def run(self):
        last = time.time()
        while self.alive:
            time.sleep(1 / 60)
            now = time.time()
            self.app.tick(min(0.1, now - last))
            last = now


def wait_quiet(m, app, t0, cap=75):
    """Insert idiom: wait until disk ops stop growing and the burst ended."""
    last, quiet = -1, 0.0
    while time.time() - t0 < cap:
        time.sleep(0.5)
        o = ops(m)
        quiet = quiet + 0.5 if o == last else 0.0
        last = o
        burst = in_burst(app, m)
        if quiet >= 1.5 and not burst:
            return True
    return False


def shot(app, path):
    surf = app.render()
    from PIL import Image
    import pygame
    raw = pygame.image.tobytes(surf, "RGB")
    Image.frombytes("RGB", surf.get_size(), raw).save(path)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--choose", type=int, default=6,
                    help="rotary detents before pushing (track row)")
    args = ap.parse_args()

    t0 = time.time()
    from emu.machine import Machine
    machine = Machine.load(CKPT)
    app = EmuApp(machine=machine, ckpt=CKPT, headless=True)
    m = app.machine
    Host(app).start()
    print(f"snapshot restored in {time.time()-t0:.1f}s", flush=True)

    # ---- insert followed by the mount burst
    t_ins = time.time()
    app.insert_usb(FOLDER)
    wait_quiet(m, app, t_ins)
    dev = m.mem.dev
    usb = dev._usb()
    ok = usb.attached and usb.stick is not None
    print(f"insert+mount: {time.time()-t_ins:.1f}s wall, mounted={ok}, "
          f"ops={ops(m)}, steps={m.cpu.steps//1_000_000}M, "
          f"status={app.status!r}", flush=True)
    shot(app, "screenshots/diag/timing_idle.png")

    # ---- Browse navigation via the on-screen button
    t_b = time.time()
    tap(app, "Browse")
    time.sleep(2.5)
    app.rotary_step(args.choose)
    t_load = time.time()
    o0 = ops(m)
    tap(app, "RotaryPush")
    peak, curt, still = o0, o0, 0.0
    while time.time() - t_load < 120:
        time.sleep(0.25)
        o = ops(m)
        if o > peak:
            peak, still = o, 0.0
        else:
            still += 0.25
        if still >= 2.0 and not in_burst(app, m):
            break
    ring = dev.usb_disk.access
    seen_paths = set()
    for e in ring:
        p = e[0]
        if p.startswith("DIR:"):
            continue
        seen_paths.add(p.rsplit("/", 2)[-2] + "/" + p.rsplit("/", 1)[-1]
                        if "/" in p else p)
    print(f"  {len(seen_paths)} distinct files read; last:", flush=True)
    for e in list(ring)[-8:]:
        print("    ", e[0][-70:], e[1], flush=True)
    print(f"track load: {time.time()-t_load:.1f}s wall "
          f"(reads moving {(ops(m) - o0):+d} ops), "
          f"steps={m.cpu.steps//1_000_000}M, "
          f"status={app.status!r}", flush=True)
    shot(app, "screenshots/diag/timing_loaded.png")

    # ---- PLAY (bursts are gated OFF while playing)
    t_play = time.time()
    tap(app, "PLAY")
    end = t_play + 12
    while time.time() < end:
        time.sleep(0.5)
    de = getattr(dev, "dspemu", None)
    if de is not None:
        out = getattr(de.asp1, "out", [])
        print(f"played 12s: asp.out={len(out)} words, "
              f"mode={m.mem.r32(0x0B6C4264)}", flush=True)
    print(f"total {time.time()-t0:.1f}s, burst_now={in_burst(app, m)}",
          flush=True)
    app._stop()


if __name__ == "__main__":
    main()
