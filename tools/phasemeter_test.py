#!/usr/bin/env python3
"""phasemeter_test.py -- Type 1 phase meter cave, emulator check.

Snapshot cache/emu_rgb_daveamstrong.pkl = main screen, "Make Your Move
(Olav Basoski Remix)" loaded (beat grid in the ANLZ ctx). Installs
build/phasemeter/pm.bin (tools/phasemeter/build.sh) at its link address,
chains the 2DG flush hook, sets the play position [0x09D246E0] to a few
values and checks the drawn red/grey tick columns on the blue row against
the Python model (tools/phase_meter.py maths, beat grid read from the
track's own .DAT). Also: Browse open -> nothing drawn over the list.
Screens: screenshots/phasemeter_*.png
"""
import os
import struct
import sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

OUT = "build/phasemeter"
HOOK = 0x08C92E20
SH_POS = 0x09D246E0
UI = 0x09D52B00
DAT = os.path.expanduser("~/Downloads/Rekordbox stuff/PIONEER/USBANLZ/P025/"
                         "00003141/ANLZ0000.DAT")
X0, X1, CX, BY = 260, 471, 366, 150
RED, TICK = 0xE145, 0x94B2       # RGB(226,40,40), RGB(150,150,150) in 565


def syms():
    out = {}
    for line in open(f"{OUT}/pm.syms"):
        a, _, n = line.split()
        out[n.lstrip("_")] = int(a, 16)
    return out


def grid():
    b = open(DAT, "rb").read()
    q = b.find(b"PQTZ")
    n = struct.unpack(">I", b[q + 20:q + 24])[0]
    return [struct.unpack(">HHI", b[q + 24 + 8 * k:q + 32 + 8 * k]) for k in range(n)]


def model(g, ms):
    """expected {x: 'red'|'tick'} on the blue row"""
    ts = [t for _, _, t in g]
    if ms < ts[0]:
        return {}
    lo = max(i for i, t in enumerate(ts) if t <= ms)
    frac = 0 if lo == len(ts) - 1 else ((ms - ts[lo]) << 8) // (ts[lo + 1] - ts[lo])
    out = {}
    for k in range(-5, 6):
        j = lo + k
        if not 0 <= j < len(ts):
            continue
        num = ((k << 8) - frac) * (X1 - X0)
        x = CX + int(num / (8 << 8))          # C division truncates toward 0
        if not X0 <= x <= X1 or abs(x - CX) <= 1:
            continue                          # off-ruler / under the white line
        out[x] = "red" if g[j][0] == 1 else "tick"
    return out


def main():
    s = syms()
    base = s["pm_entry"]
    img = open(f"{OUT}/pm.bin", "rb").read()
    m = Machine.load("cache/emu_rgb_daveamstrong.pkl")
    mem, c = m.mem, m.cpu
    end = s["pm_mode"] + 4
    assert all(mem.r8(a) == 0 for a in range(base, end, 4)), "cave not empty"
    for i, b in enumerate(img):
        mem.w8(base + i, b)
    prev = mem.r32(HOOK)
    mem.w32(s["pm_prev"], prev)
    mem.w8(s["pm_mode"], 1)
    mem.w32(HOOK, base)
    print(f"cave {base:#x}..{end:#x}, chained prev {prev:#x}")
    g = grid()
    ok = True
    for i, pos in enumerate((1500, 1537, 2200, 4000)):
        mem.w32(SH_POS, pos)
        d0 = mem.r32(s["pm_draws"])
        m.cpu.run(limit=30_000_000)
        d1 = mem.r32(s["pm_draws"])
        L = mem.r32(s["pm_layer"]) & 0x1FFFFFFF
        print("   layer", hex(L), "row145 sample", [hex(mem.r16(L + (145 * 800 + x) * 2)) for x in (270, 297, 298, 323)])
        print(f"   after run: SH_POS={mem.r32(SH_POS):#x} pm_n={mem.r32(s['pm_n'])}")
        fb_png(m, f"screenshots/phasemeter_{i}.png")
        # check the COMPOSED screen: the stock painter still repaints its
        # boxes into the layer between flushes; our hook redraws inside
        # every flush, so every frame that reaches the LCD carries ours
        from PIL import Image
        im = Image.open(f"screenshots/phasemeter_{i}.png").convert("RGB")
        y = BY - 5
        got = {}
        for x in range(X0, X1 + 1):
            r, g_, b = im.getpixel((x, y))
            if r > 180 and g_ < 90 and b < 90:
                got[x] = "red"
            elif 130 <= r <= 170 and abs(r - g_) < 12 and abs(r - b) < 12:
                got[x] = "tick"
        red_x = sorted({x if got.get(x) == "red" and got.get(x - 1) != "red" else None
                        for x in got} - {None})
        want = model(g, pos * 40 // 3)
        # red is drawn 2 px wide (x-1, x): compare by the right column
        got_n = {x: v for x, v in got.items() if not (v == "red" and got.get(x + 1) == "red")}
        match = got_n == want
        ok &= match and d1 > d0
        print(f"pos {pos} ({pos*40//3} ms): draws +{d1-d0}, "
              f"{'MATCH' if match else 'DIFF'} got {sorted(got_n.items())} want {sorted(want.items())}")
    # Browse open: the list covers the meter -> must not be painted over
    mem.dev.panel_frame = panl.build_frame(keys=["Browse"])
    m.cpu.run(limit=3_000_000)
    mem.dev.panel_frame = panl.build_frame()
    d0 = mem.r32(s["pm_draws"])
    m.cpu.run(limit=30_000_000)
    d1 = mem.r32(s["pm_draws"])
    fb_png(m, "screenshots/phasemeter_browse.png")
    print(f"browse open: draws +{d1 - d0} (want 0)")
    ok &= d1 == d0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
