#!/usr/bin/env python3
"""permanent_extras_emu.py SNAPSHOT -- EMULATOR ONLY: do the boot-copied
extras of a permanent build WORK, with nothing installed over USB?

SNAPSHOT = a cold boot of the built file, saved by
    SAVE=booted.pkl tools/permanent_build.py --emu OUT.UPD
(HLE DSP). Mounts STICK (default cache/stick7.img, the analysed track the
real-DSP tests use), opens USB and loads with PUSHES rotary pushes (7),
then checks on the deck's
own screens and buttons:
  * centre waveform colours: the colour module is called (cw_calls) and
    the centre band on the main screen is not stock blue,
  * phase meter drawn (pm_draws), live-stems read hook ran (sm_calls),
  * PERFORM: HOT CUE tab swipe pages to D E F (8 hot cues), letters drawn,
  * KEY SHIFT tab in REAL-TIME mode (the BD8 store NOP is baked): > steps
    RTKEY and writes the tagged key word the DSP code reads.
The audible key shift itself needs the real DSP: boot with XDJ_DSP_EMU=1,
play a track (tools/dsp_play_test.py) and run tools/keyshift_dsp_emu.py on
that snapshot -- it reports "already runs the key-shift code".
Screens: screenshots/permanent_extras/.
"""
import os
import sys

if not os.environ.get("XDJ_DSP_EMU"):          # real-DSP snapshots carry their own
    os.environ.setdefault("XDJ_HLE_DSP", "1")
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import perform_pads as PP                # noqa: E402
import importlib.util                    # noqa: E402
_spec = importlib.util.spec_from_file_location(   # the builder next to this file
    "permanent_build", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "permanent_build.py"))
PB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PB)

OUT = os.environ.get("XDJ_SHOTS", "screenshots/permanent_extras")
SLOT = 0x0B6C4118                        # hot cue pad slots (hotcue8_test)
ROT = 0x0B6C7B48


def main():
    import numpy as np
    from PIL import Image
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    os.makedirs(OUT, exist_ok=True)
    stick = os.environ.get("STICK", "cache/stick7.img")   # analysed Get Lucky
    pushes = int(os.environ.get("PUSHES", "7"))          # USB + n pushes loads
    m = Machine.load(sys.argv[1])
    mem, dev = m.mem, m.mem.dev
    e0 = m.exc_total
    _, extras = PB.baked_jobs(bytes(mem.r_block(0x08000000, 0x011BD788 + 0x8000)))
    print("baked extras:", sorted(extras))
    ok = True

    def chk(label, cond):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label, flush=True)
        ok &= bool(cond)

    def run(n):
        m.cpu.run(limit=int(n))

    def key(k, n=15e6):
        dev.panel_frame = panl.build_frame(keys=[k])
        run(3e6)
        dev.panel_frame = panl.build_frame()
        run(n)

    def tap(x, y, hold=12e6, after=30e6):
        dev.panel_frame = panl.build_frame(touch=(x, y))
        run(hold)
        dev.panel_frame = panl.build_frame()
        run(after)

    s = {k: v for e in extras.values() for k, v in e.items()}
    calls0 = {k: mem.r32(s[k]) for k in ("cw_calls", "pm_draws", "sm_calls", "hc_draws")
              if k in s}
    # ---- mount the stick, browse, load (dsp_play_test choreography);
    # a snapshot that already plays a track (real DSP: the HLE model can
    # never finish a load, E-8302, CLAUDE.md §99) skips straight to checks
    preloaded = mem.r32(0x0BA3C050) != 0
    if not preloaded:
        u = dev._usb()
        u.unplug()
        run(20e6)
        disk = open_disk(stick)
        dev.usb_disk = disk
        u.stick = MassStorage(disk)
        for _ in range(6):
            run(250e6)
        if not u.stick.addr:                       # boot-attach quirk (CLAUDE.md §32)
            u.unplug()
            run(100e6)
            for _ in range(5):
                run(200e6)
        print("stick addr", u.stick.addr, flush=True)
        key("DevUSB", 120e6)                       # selects USB, opens the browser
        for _ in range(pushes - 1):
            key("RotaryPush", 80e6)
        fb_png(m, f"{OUT}/0_list.png")
        key("RotaryPush", 400e6)               # the last push loads
        run(300e6)
    run(120e6)
    fb_png(m, f"{OUT}/1_main.png")
    if os.environ.get("SAVE_LOADED"):
        m.save(os.environ["SAVE_LOADED"])
    chk("track loaded", mem.r32(0x0BA3C050) != 0)
    if "centre" in extras:
        n = mem.r32(s["cw_calls"]) - calls0["cw_calls"]
        a = np.array(Image.open(f"{OUT}/1_main.png").convert("RGB")).astype(int)
        band = a[165:315, 250:620, :]          # centre waveform area
        lit = band.max(axis=2) > 80
        blue = lit & (band[..., 2] > band[..., 0] + 40) & (band[..., 2] > band[..., 1] + 20)
        warm = lit & (band[..., 0] > band[..., 2] + 40)
        print(f"  centre band: lit {int(lit.sum())}, blue {int(blue.sum())}, "
              f"red/orange {int(warm.sum())}; cw_col calls {n}")
        chk("centre colours: colour module drives the painter", n > 0)
        chk("centre colours: drawn not stock blue", warm.sum() > 200)
    if "phasemeter" in extras:
        chk("phase meter drawn", mem.r32(s["pm_draws"]) > calls0["pm_draws"])
    if "stems" in extras:
        chk("live stems read hook ran on the load",
            mem.r32(s["sm_calls"]) > (0 if preloaded else calls0["sm_calls"]))
    # ---- PERFORM
    tap(709, 37)
    chk("PERFORM opens", mem.r32(0x0BA371D4) == 7)
    tab = (PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[0] + PP.TAB_H // 2)
    tap(*tab)
    if "hotcue8" in extras:
        chk("8 hot cues: hook runs (hashed this track)", mem.r32(s["hc_hash"]) != 0)
        for x in (100, 65, 30):                # swipe left along the tab
            dev.panel_frame = panl.build_frame(touch=(x, tab[1]))
            run(8e6)
        dev.panel_frame = panl.build_frame()
        run(60e6)
        n = mem.r32(s["hc_count"])
        slots = [mem.r16(SLOT + k * 0x40 + 0x0A) for k in range(3)]
        print(f"  hot cues on this track: {n}; page {mem.r8(PP.HCPAGE)} slots {slots}")
        fb_png(m, f"{OUT}/2_hotcue_swipe.png")
        if n > 3:
            chk("8 hot cues: swipe pages to D E F (page 2)", mem.r8(PP.HCPAGE) == 1
                and slots[0] == 4)
            chk("8 hot cues: letters drawn", mem.r32(s["hc_draws"]) > calls0["hc_draws"])
        else:
            chk("8 hot cues: <= 3 cues, swipe correctly does nothing "
                "(paging NOT exercised on this track)", mem.r8(PP.HCPAGE) == 0)
    if "keyshift" in extras:
        tap(PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[2] + PP.TAB_H // 2)
        L, M, R = ((b[0] + b[2] // 2, b[1] + b[3] // 2) for b in (PP.KSL, PP.KSM, PP.KSR))
        tap(*R)
        tap(*R)
        run(30e6)
        v = mem.r8(PP.RTKEY)
        w = mem.r32(PP.RT_KEYWORD)
        print(f"  KEY SHIFT: RTMODE {mem.r8(PP.RTMODE)} RTKEY {v} word {w:#010x}")
        fb_png(m, f"{OUT}/3_keyshift_plus2.png")
        chk("KEY SHIFT real-time: > > = +2, tagged key word written",
            v == 2 and w == PP.KQWORDS[8])
        tap(*M)
        chk("KEY SHIFT RESET", mem.r8(PP.RTKEY) == 0)
    chk(f"no CPU exceptions ({m.exc_total - e0} new)", m.exc_total == e0)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
