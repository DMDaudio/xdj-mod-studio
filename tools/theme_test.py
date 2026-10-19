#!/usr/bin/env python3
"""theme_test.py THEME.UPD [...]  -- boot-test themes in the emulator.

Boots stock and each theme from reset to the same instruction count (the
emulator is deterministic, so both screens show the same UI state) and
compares them:

  missed   pixels that are stock-UI blue (the band the theme engine moves)
           and still exactly stock in the theme -- unthemed leftovers
  changed  pixels that differ at all

A theme that crashes the firmware (no screen by the end) is reported as
CRASHED -- the emulator catching a would-be brick. Writes a side-by-side
PNG per theme to build/theme_test/.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mods"))

STEPS = 450_000_000
OUT = os.path.join(ROOT, "build", "theme_test")


def boot(upd):
    from emu import fw as F
    from emu.machine import Machine
    flash, info = F.load_flash(upd)
    m = Machine(flash)
    while m.cpu.steps < STEPS:
        m.run(limit=min(20_000_000, STEPS - m.cpu.steps + 1))
    reg = m.mem.dev.reg
    base = sum(reg.get(0xFE940430 + i, 0) << (8 * i) for i in range(4))
    idx = 1 if (base & 0x1FFFFFFF) == 0x0E83B800 else 0
    fb = np.frombuffer(bytes(m.mem.fb[idx]), dtype="<u2").reshape(480, 800)
    return fb, m.exc_total


def rgb(fb):
    v = fb.astype(np.uint32)
    return np.dstack([((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63,
                      (v & 31) * 255 // 31]).astype(np.uint8)


def main():
    import themes as T
    from PIL import Image
    os.makedirs(OUT, exist_ok=True)
    stock, _ = boot(os.path.join(ROOT, "FIRMWARE", "XDJ700_stock_v1.15.UPD"))
    s = rgb(stock).astype(np.float32)
    h, sat, _ = T._to_hsv(s[..., 0], s[..., 1], s[..., 2])
    blue = (h >= T.HUE_LO) & (h <= T.HUE_HI) & (sat >= T.SAT_MIN)
    for upd in sys.argv[1:]:
        name = os.path.basename(upd)[:-4]
        fb, excs = boot(upd)
        if not fb.any():
            print(f"{name:28} CRASHED (no screen, {excs} exceptions)")
            continue
        missed = blue & (fb == stock)
        changed = fb != stock
        print(f"{name:28} missed {missed.sum():6,} of {blue.sum():,} blue px "
              f"({missed.sum() / max(1, blue.sum()):.1%})   changed {changed.sum():,}"
              f"   exceptions {excs}")
        img = np.concatenate([rgb(stock), np.zeros((480, 8, 3), np.uint8), rgb(fb)], 1)
        mk = rgb(fb).copy()
        mk[missed] = (255, 255, 0)
        img = np.concatenate([img, np.zeros((480, 8, 3), np.uint8), mk], 1)
        Image.fromarray(img).save(os.path.join(OUT, f"{name}.png"))


if __name__ == "__main__":
    main()
