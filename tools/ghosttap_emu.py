#!/usr/bin/env python3
"""ghosttap_emu.py -- fire the deck ghost-tap recipes INSIDE the emulator.

Phases (each ~2M steps, PNG screenshot after):
  0 control        just run
  1 v3 replica     volley PAY+4/5 (fader as v3 did), Y, P + 4 mirror bytes
  2 v4 replica     volley PAY+21 down byte, Y, P + mirrors (no fader)
  3 payload-bit    hold the Browse on-screen button bit in the frame,
                   then release (panel-MCU route -- should react)

Metrics: touchdisp hits, PAY/mirrors/slots/key-bitmap, fb byte-delta,
mode word. PNGs saved to screenshots/ghosttap_emu_<phase>.png.
"""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from emu.machine import Machine
from emu import panl

CKPT = os.path.expanduser("~/emu-ckpt/emu_clean4.pkl")
FB_W, FB_H = 800, 480
PAY = 0x0B6C7A30
MIRRORS = [(0x0B6C7A64, 0xEC), (0x0B6C7A82, 0x80),
           (0x0B6C7A98, 0xF0), (0x0B6C7A9C, 0xFC)]


def fb_png(m, path):
    reg = m.mem.dev.reg
    base = sum(reg.get(0xFE940430 + i, 0) << (8 * i) for i in range(4))
    idx = 1 if (base & 0x1FFFFFFF) == 0x0E83B800 else 0
    buf = bytes(m.mem.fb[idx])
    if not any(buf[::97]):
        buf = bytes(m.mem.fb[1 - idx])
    a = np.frombuffer(buf, dtype="<u2").astype(np.uint32)
    r = ((a >> 11) & 0x1F); g = ((a >> 5) & 0x3F); b = (a & 0x1F)
    rgb = np.dstack([
        ((r * 255 + 15) // 31).astype(np.uint8).reshape(FB_H, FB_W),
        ((g * 255 + 31) // 63).astype(np.uint8).reshape(FB_H, FB_W),
        ((b * 255 + 15) // 31).astype(np.uint8).reshape(FB_H, FB_W)])
    Image.fromarray(rgb).save(path)
    return buf


def rd(mem, a, n):
    return " ".join(f"{mem.r8(a + i):02x}" for i in range(n))


def metrics(m, tag, fb0):
    mem = m.mem
    fb1 = fb_png(m, f"screenshots/ghosttap_emu_{tag}.png")
    diff = sum(1 for x, y in zip(fb0, fb1) if x != y)
    print(f"[{tag}] touchdisp={TD[0]} fbdiff={diff} mode="
          f"{rd(mem, 0x0B6C4264, 1)}")
    print(f"   PAY+20..26={rd(mem, PAY + 20, 7)} "
          f"mirrors={mem.r8(0x0B6C7A64):02x}/{mem.r8(0x0B6C7A82):02x}/"
          f"{mem.r8(0x0B6C7A98):02x}/{mem.r8(0x0B6C7A9C):02x}")
    print(f"   keybits 0B6C7A7C..93={rd(mem, 0x0B6C7A7C, 24)}")
    return fb1


TD = [0]


def run_phase(m, tag, volley=None, frame=None, volleys=50, slice_steps=40_000):
    c = m.cpu
    TD[0] = 0

    def tr(pc, op):
        if pc == 0x08D2FE5C:
            TD[0] += 1
    c.trace = tr
    fb0 = fb_png(m, f"screenshots/ghosttap_emu_{tag}_before.png")
    if volley:
        for i in range(volleys):
            volley(m)
            c.run(limit=slice_steps)
        # release volley: restore mirrors to resting-ish, no touch
        for a, _ in MIRRORS:
            m.mem.w8(a, 0x00)
        m.mem.w8(PAY + 21, 0)
        c.run(limit=slice_steps)
    elif frame is not None:
        m.mem.dev.panel_frame = frame
        c.run(limit=slice_steps * volleys // 2)
        m.mem.dev.panel_frame = panl.build_frame()   # release
        c.run(limit=slice_steps)
    else:
        c.run(limit=slice_steps * volleys)
    c.trace = None
    return metrics(m, tag, fb0)


def v3_volley(m):
    for a, v in [(PAY + 4, 0x7F), (PAY + 5, 0xAC), (PAY + 22, 0x03),
                 (PAY + 23, 0x9D), (PAY + 24, 0x02), (PAY + 25, 0xF2)]:
        m.mem.w8(a, v)
    for a, v in MIRRORS:
        m.mem.w8(a, v)


def v4_volley(m):
    for a, v in [(PAY + 21, 0x01), (PAY + 22, 0x03), (PAY + 23, 0x9D),
                 (PAY + 24, 0x02), (PAY + 25, 0xF2)]:
        m.mem.w8(a, v)
    for a, v in MIRRORS:
        m.mem.w8(a, v)


def browse_frame():
    fr = bytearray(panl.build_frame())
    p = bytearray(fr[:30])
    p[20] |= 0x01          # Browse bit (payload 20, bit0)
    out = bytearray(p)
    out.append(panl.checksum(p))
    out.append(0x8F)
    return bytes(out)


def main():
    os.makedirs("screenshots", exist_ok=True)
    m = Machine.load(CKPT, core="py")
    run_phase(m, "0_control")
    run_phase(m, "1_v3replica", volley=v3_volley)
    run_phase(m, "2_v4replica", volley=v4_volley)
    run_phase(m, "3_browsebit", frame=browse_frame())
    print("PNGs in screenshots/ghosttap_emu_*.png")


if __name__ == "__main__":
    main()
