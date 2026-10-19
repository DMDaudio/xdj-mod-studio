#!/usr/bin/env python3
"""aiff_repro.py -- reproduce the user's E-8302 with HIS rekordbox stick.

Restores the verified DSP snapshot, mounts the real library folder,
then walks the browse with a screenshot after every step so we can see
exactly which screen the deck is on. LOAD=1 does the final load push and
reports the E-8302 red-text count, SH position, DSP health and DAC words.

Env:
  SNAP   snapshot (default cache/emu_dsp_sound.pkl.gz)
  STICK  library folder (default the user's rekordbox export)
  NPUSH  rotary pushes after DevUSB (default 6, screenshot each)
  LOAD   1 = the last push is the LOAD push at real DSP speed
  ROWS   rotate down ROWS rows between browse and load (default 0)
  SAVE_MOUNTED=path  save state right after mount (iterate from there)
  MOUNTED=path       skip mounting, restore this state instead
"""
import os
import sys
import time

sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402
from PIL import Image                    # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402

SNAP = os.environ.get("SNAP", "cache/emu_dsp_sound.pkl.gz")
STICK = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
NPUSH = int(os.environ.get("NPUSH", "6"))
LOAD = os.environ.get("LOAD") == "1"
ROWS = int(os.environ.get("ROWS", "0"))
PLAY_STEPS = int(float(os.environ.get("PLAY_STEPS", "1.2e9")))
ROT = 0x0B6C7B48
SH_POS = 0x09D246E0
MODE = 0x0B6C4264


def red_text(m, name):
    p = f"screenshots/aiff_{name}.png"
    fb_png(m, p)
    a = np.array(Image.open(p).convert("RGB")).astype(int)
    r = a[398:428, 170:600]
    return int(((r[..., 0] > 180) & (r[..., 1] < 90) & (r[..., 2] < 90)).sum())


def state(m, d, tag):
    print(f"{tag}: red={red_text(m, tag)} pos={m.mem.r32(SH_POS)} "
          f"mode={m.mem.r8(MODE)} dac={len(d.asp1.out)} "
          f"dsp err={d.error}", flush=True)


def main():
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    from emu.c6x.dspdev import DSP_PER_SH
    t0 = time.time()
    mounted = os.environ.get("MOUNTED")
    if mounted:
        m = Machine.load(mounted)
    else:
        m = Machine.load(SNAP)
        mem, dev = m.mem, m.mem.dev
        d = dev.dspemu
        assert d is not None, "snapshot has no real DSP"
        d.ratio = 0.1                              # fast while mounting
        u = dev._usb()
        u.unplug()
        m.cpu.run(limit=20_000_000)
        disk = open_disk(STICK)
        dev.usb_disk = disk
        u.stick = MassStorage(disk)
        for _ in range(6):
            m.cpu.run(limit=250_000_000)
            if u.stick.addr:
                break
        if not u.stick.addr:                       # boot-attach quirk
            u.unplug()
            m.cpu.run(limit=100_000_000)
            for _ in range(5):
                m.cpu.run(limit=200_000_000)
        print(f"stick addr {u.stick.addr} "
              f"disk={len(disk.access)} sectors served "
              f"[{time.time()-t0:.0f}s]", flush=True)
        sm = os.environ.get("SAVE_MOUNTED")
        if sm:
            m.save(sm)
            print("saved", sm, flush=True)
    mem, dev = m.mem, m.mem.dev
    d = dev.dspemu
    hpi = []                                        # SH<->DSP hand-off log
    cmdlog = []
    if os.environ.get("TRACE_CMD"):
        ost = d._store

        def stw(a, v, ost=ost):
            if 0x11837BC0 <= a < 0x11837C64:
                cmdlog.append((hex(a), hex(v)))
            return ost(a, v)
        d._store = stw
    if os.environ.get("TRACE_HPI"):
        ob, orr = d.bulk, d.read

        def bk(data, ob=ob):
            hpi.append(("bulk", len(data), hex(d.hpia)))
            return ob(data)

        def rd(a, n, orr=orr):
            h = d.hpia
            v = orr(a, n)
            if a >= 0x18080000 and (0x11838180 <= h < 0x118381C0 or h in (
                    0x11837BF8, 0x11837CC8, 0x11837CCC, 0x11837CD0,
                    0x11837CD4, 0x11838140)):
                if not hpi or hpi[-1][2:] != (hex(h), v):
                    hpi.append(("rd", hex(h), v))
            return v
        d.bulk, d.read = bk, rd

    def run(n):
        m.cpu.run(limit=int(n))

    def key(k, n=15e6):
        dev.panel_frame = panl.build_frame(keys=[k])
        run(3e6)
        dev.panel_frame = panl.build_frame()
        run(n)

    key("DevUSB", 120e6)
    state(m, d, "usb")
    n = NPUSH if not LOAD else NPUSH - 1
    for k in range(1, n + 1):
        key("RotaryPush", 80e6)
        state(m, d, f"push{k}")
    for _ in range(ROWS):
        mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)
        run(20e6)
    if os.environ.get("WAIT"):                 # poll for the screen to move
        chunk = int(float(os.environ.get("WAIT_CHUNK", "2e8")))
        steps = int(float(os.environ.get("WAIT_STEPS", "15")))
        prev = None
        for k in range(1, steps + 1):
            run(chunk)
            p = f"screenshots/aiff_wait{k}.png"
            fb_png(m, p)
            from PIL import Image as _I
            import hashlib
            h = hashlib.md5(open(p, "rb").read()).hexdigest()[:8]
            acc = len(dev.usb_disk.access) if getattr(dev, "usb_disk", None) else -1
            print(f"  wait{k} h={h} rot={mem.r16(ROT):04x} pos={mem.r32(SH_POS)} "
                  f"mode={mem.r8(MODE)} dac={len(d.asp1.out)} "
                  f"dsp err={d.error} disk={acc}", flush=True)
            if prev == h:
                pass
            prev = h
    if LOAD:
        run(40e6)
        state(m, d, "pre_load")
        d.ratio = DSP_PER_SH                      # real speed from the load on
        key("RotaryPush", 300e6)                  # the load
        state(m, d, "loaded")
        last, out = {}, []
        for a, v in cmdlog:
            if last.get(a) == v:
                continue
            last[a] = v
            out.append(f"{a}={v}")
        print("CMD:", " ".join(out[-40:]), flush=True)
        print("HPI tail:", hpi[-25:], flush=True)
        n0 = len(d.asp1.out)
        dev.panel_frame = panl.build_frame(keys=("PlayPause",), tempo=250)
        run(3e6)
        dev.panel_frame = panl.build_frame(tempo=250)
        done = 0
        acc0 = len(dev.usb_disk.access) if dev.usb_disk else 0
        cyc0 = d.cycles
        u = dev._usb()
        usb_rd = [0]
        _urd = u.read

        def urd(a, s, _urd=_urd):
            usb_rd[0] += 1
            return _urd(a, s)
        u.read = urd
        HS = (0x11837BF8, 0x11837CC8, 0x11837CCC, 0x11837CD0, 0x11837CD4,
              0x11838140, 0x11838180, 0x11838184, 0x11838188, 0x1183818C)
        while done < PLAY_STEPS:
            run(100e6)
            done += 100e6
            nz = sum(1 for v in d.asp1.out[n0:] if v)
            acc = len(dev.usb_disk.access) if dev.usb_disk else -1
            words = " ".join(f"{a:08x}={mem.r32(a):08x}" for a in HS)
            print(f"  play +{done/1e6:.0f}M pos={mem.r32(SH_POS)} "
                  f"mode={mem.r8(MODE)} dac {len(d.asp1.out) - n0} "
                  f"nonzero {nz} dsp err={d.error} disk={acc} (+{acc-acc0}) "
                  f"usbrd={usb_rd[0]} dspcyc+{(d.cycles-cyc0)/1e6:.0f}M "
                  f"[{time.time()-t0:.0f}s]", flush=True)
            usb_rd[0] = 0
            cyc0 = d.cycles
            if d.error:
                break
        out = d.asp1.out[n0:]
        nz = sum(1 for v in out if v)
        print(f"RESULT: dac words {len(out)} nonzero {nz} pos {mem.r32(SH_POS)} "
              f"dsp err {d.error}", flush=True)
    print(f"done [{time.time()-t0:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
