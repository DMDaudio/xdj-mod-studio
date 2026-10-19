"""Real emulator captures for the promo clip (tools/promo_video.py).

    .venv/bin/python tools/promo_capture.py

cache/user_stick_mounted.pkl (real DSP + the user's library mounted) ->
the emulator app's mod install (3-band) -> BROWSE > ARTIST > row ART_ROW >
load "Don't Come Back" -> it auto-plays -> screens of the main view (3-band
and RGB centre), then PERFORM with the BEAT JUMP / KEY SHIFT / STEMS tabs.
Writes screenshots/promo/*.png (firmware-drawn pixels: never public).
"""
import os
import sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl, ui                 # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import perform_pads as PP                # noqa: E402
import rgb_overview as RO                # noqa: E402

SNAP = os.environ.get("SNAP", "cache/user_stick_mounted.pkl")
ART_ROW = int(os.environ.get("ART_ROW", "28"))
OUT = os.environ.get("OUT", "screenshots/promo")
os.makedirs(OUT, exist_ok=True)

m = Machine.load(SNAP)
mem, dev = m.mem, m.mem.dev
d = dev.dspemu


def run(n, frame=None):
    dev.panel_frame = frame or panl.build_frame()
    m.cpu.run(limit=int(n))


def key(k, n=80e6):
    run(5e6, panl.build_frame(keys=[k]))
    run(n)


def rot(n):
    for _ in range(abs(n)):
        mem.w16(panl.ROTARY_COUNTER, (mem.r16(panl.ROTARY_COUNTER) + (1 if n > 0 else -1)) & 0xFFFF)
        run(25e6)


def touch(xy, n=60e6):
    run(4e6, panl.build_frame(touch=xy))
    run(n)


def shot(name):
    fb_png(m, f"{OUT}/{name}.png")
    print("shot", name, "dsp", d.bus.read(0x11837BF8, 4), flush=True)


app = ui.EmuApp.__new__(ui.EmuApp)
app.machine, app.fw_name, app.status = m, "Stock v1.15", ""
app.apply_wave(os.environ.get("WAVE", "rgb"))
print("mods:", app.status, flush=True)

if os.environ.get("STICK"):         # swap in another stick folder (remount)
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    u = dev._usb()
    u.unplug()
    run(20e6)
    disk = open_disk(os.environ["STICK"])
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(8):
        run(250e6)
    print("stick addr", u.stick.addr, flush=True)

key("DevUSB", 150e6)
for st in os.environ.get("NAV", f"push,rot{ART_ROW},push").split(","):
    if st == "push":
        key("RotaryPush")
    else:
        rot(int(st[3:]))
shot("browse")
key("RotaryPush", 300e6)            # load
for _ in range(15):
    run(200e6)
    if d.bus.read(0x11837BF8, 4) == 2:
        break
TX = PP.TAB_X + PP.TAB_W // 2
BJT = (TX, PP.TAB_Y[1] + PP.TAB_H // 2)
if os.environ.get("JUMPS"):         # e.g. "5,1" = >>64 then >>16 (page 16-32-64)
    touch((718, 38))                # PERFORM
    for _ in range(3):              # BEAT JUMP tab, then page to 16-32-64
        touch(BJT)
    for i in os.environ["JUMPS"].split(","):
        x, w = PP.ITEMS[int(i)]
        touch((x + w // 2, PP.HC_Y + PP.HC_H // 2), 80e6)
    touch(BJT)                      # page wraps back to 1-2-4
    touch((718, 38))                # PERFORM off
for _ in range(int(float(os.environ.get("PLAYFOR", "0")) * 2)):
    run(250e6)                      # 0.5 s of deck time
run(600e6)
print("position", d.bus.read(0x11837CCC, 4) / 75.0, "s", flush=True)
shot("main_a")
run(300e6)
shot("main_b")


touch((718, 38))                    # PERFORM
shot("perform")


def pad(i):
    x, w = PP.ITEMS[i]
    return (x + w // 2, PP.HC_Y + PP.HC_H // 2)


def held(xy, name):                 # screenshot while the finger is down
    run(30e6, panl.build_frame(touch=xy))
    shot(name)
    run(60e6)


touch(BJT)                          # BEAT JUMP, page 1-2-4
touch(BJT)                          # page 4-8-16
shot("perform_beatjump")
held(pad(4), "bj_back16")           # << 16
held(pad(5), "bj_fwd16")            # >> 16
touch(BJT)                          # page 16-32-64
touch(BJT)                          # back to 1-2-4
touch((TX, PP.TAB_Y[2] + PP.TAB_H // 2))      # KEY SHIFT
shot("perform_keyshift")
for _ in range(2):
    touch((551, 264))               # >
shot("ks_up2")
for _ in range(4):
    touch((181, 264))               # <
shot("ks_dn2")
touch((365, 264))                   # RESET
touch((TX, PP.TAB_Y[3] + PP.TAB_H // 2))      # STEMS
shot("perform_stems")
x, w = PP.STEMS5[0]
held((x + 50, PP.HC_Y + PP.HC_H // 2), "perform_stems_vocals_tap")
shot("perform_stems_vocals_off")

# themes: the exact recolour the deck's [THEME] push does (theme_switch
# model, emu-verified == deck C), applied to the artwork in RAM
import numpy as np                       # noqa: E402
import theme_switch as TS                # noqa: E402
NAMES = ["hot-pink", "ice-cyan", "amber-gold"]
regs, S, Ds, _ = TS.tables(NAMES)
cur = 0
for k, nm in enumerate(NAMES if not os.environ.get("NO_THEMES") else [], 1):
    frm, to = (S if cur == 0 else Ds[cur - 1]), Ds[k - 1]
    lut = np.arange(65536, dtype=np.uint16)
    lut[frm] = to
    for a, n in regs:
        w = np.frombuffer(bytes(mem.ram0[a - 0x08000000:a - 0x08000000 + 2 * n]), "<u2")
        mem.ram0[a - 0x08000000:a - 0x08000000 + 2 * n] = lut[w].astype("<u2").tobytes()
    cur = k
    run(200e6)
    shot(f"theme_{nm}_perform")
    touch((718, 38))                # PERFORM off -> main view
    shot(f"theme_{nm}_main")
    touch((718, 38))                # back to PERFORM for the next theme
print("done")
