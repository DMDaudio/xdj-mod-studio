"""Continuous emulator recording for the promo (moving waveform).

    STICK=<copy> .venv/bin/python tools/promo_record.py

Loads "Get Lucky (ESQUIRE)" (TRACK list row NAV), plays to PLAYFOR s, then
records FPS frames per deck-second while doing what each scene of
tools/promo_video.py shows, at the scene's own times:
  waveform  blue -> RGB -> 3-band (centre waveform changes live)
  phase meter (3-band)
  themes    hot-pink -> ice-cyan -> amber-gold (deck's theme maths), stock
  BEAT JUMP << 16, >> 16      KEY SHIFT +2 then -2      STEMS vocals off
  HOT CUE page
Frames -> screenshots/promo_rec/NNNNN.png, per-frame deck state (playhead
block, key step, stem mask) -> build/promo_rec.json for the soundtrack.
"""
import json
import os
import sys
sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu import panl, ui                 # noqa: E402
from ghosttap_emu import fb_png          # noqa: E402
import perform_pads as PP                # noqa: E402
import rgb_overview as RO                # noqa: E402
import theme_switch as TS                # noqa: E402

FPS = int(os.environ.get("FPS", "15"))
SH_PER_S = 500e6
OUT = "screenshots/promo_rec"
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    os.remove(os.path.join(OUT, f))

m = Machine.load(os.environ.get("SNAP", "cache/user_stick_mounted.pkl"))
mem, dev = m.mem, m.mem.dev
d = dev.dspemu
cur_frame = panl.build_frame()


def run(n):
    dev.panel_frame = cur_frame
    m.cpu.run(limit=int(n))


app = ui.EmuApp.__new__(ui.EmuApp)
app.machine, app.fw_name, app.status = m, "Stock v1.15", ""
app.apply_wave("3band")
from emu.usbdisk import open_disk          # noqa: E402
from emu.usbhost import MassStorage        # noqa: E402
u = dev._usb()
u.unplug()
run(20e6)
disk = open_disk(os.environ["STICK"])
dev.usb_disk = disk
u.stick = MassStorage(disk)
for _ in range(8):
    run(250e6)


def key(k, n=80e6):
    global cur_frame
    cur_frame = panl.build_frame(keys=[k]); run(5e6)
    cur_frame = panl.build_frame(); run(n)


key("DevUSB", 150e6)
for st in os.environ.get("NAV", "rot2,push,rot86").split(","):
    if st == "push":
        key("RotaryPush")
    else:
        for _ in range(int(st[3:])):
            mem.w16(panl.ROTARY_COUNTER, (mem.r16(panl.ROTARY_COUNTER) + 1) & 0xFFFF)
            run(25e6)
key("RotaryPush", 300e6)
for _ in range(20):
    run(200e6)
    if d.bus.read(0x11837BF8, 4) == 2:
        break
for _ in range(int(float(os.environ.get("PLAYFOR", "38")) * 2)):
    run(250e6)

# ---- timeline recorder ----------------------------------------------------
D = [2.6, 4.2, 3.4, 4.5, 3.6, 3.6, 3.6, 3.6, 2.6, 3.0]
T = np.cumsum([0] + D)
REC0, REC1 = T[1], T[8]                  # record the scenes between the cards
frames, state = [], []
dac, dac_n0 = [], len(d.asp1.out)        # the deck's own audio output
t_deck = 0.0                              # video seconds since REC0
STEP = SH_PER_S / FPS


def tick():
    global t_deck, dac_n0
    run(STEP)
    dac.extend(d.asp1.out[dac_n0:])
    dac_n0 = len(d.asp1.out)
    t_deck += 1.0 / FPS
    i = len(frames)
    fb_png(m, f"{OUT}/{i:05d}.png")
    frames.append(i)
    state.append({"t": REC0 + t_deck, "blk": d.bus.read(0x11837CCC, 4),
                  "key": int(np.int8(mem.r8(PP.PST + 60))), "mask": mem.r8(PP.PMASK) & 15,
                  "dsp": d.bus.read(0x11837BF8, 4)})


def until(t):
    while REC0 + t_deck < t - 1e-6:
        tick()


def tap(xy, hold_frames=2):
    """finger down for hold_frames, then up (recorded)"""
    global cur_frame
    cur_frame = panl.build_frame(touch=xy)
    for _ in range(hold_frames):
        tick()
    cur_frame = panl.build_frame()
    tick()


TX = PP.TAB_X + PP.TAB_W // 2
TAB = lambda i: (TX, PP.TAB_Y[i] + PP.TAB_H // 2)     # noqa: E731


def pad(i):
    x, w = PP.ITEMS[i]
    return (x + w // 2, PP.HC_Y + PP.HC_H // 2)


regs, S, Ds, _ = TS.tables(["hot-pink", "ice-cyan", "amber-gold"])
theme_cur = 0


def theme(k):                            # 0 = stock
    global theme_cur
    frm = S if theme_cur == 0 else Ds[theme_cur - 1]
    to = S if k == 0 else Ds[k - 1]
    lut = np.arange(65536, dtype=np.uint16)
    lut[frm] = to
    for a, n in regs:
        o = a - 0x08000000
        w = np.frombuffer(bytes(mem.ram0[o:o + 2 * n]), "<u2")
        mem.ram0[o:o + 2 * n] = lut[w].astype("<u2").tobytes()
    theme_cur = k


W, PM, TH, BJ, KS, ST, MORE = T[1], T[2], T[3], T[4], T[5], T[6], T[7]
mem.w8(RO.MODE_ADDR, RO.MODES["blue"])
until(W + D[1] / 3)
mem.w8(RO.MODE_ADDR, RO.MODES["rgb"])
until(W + 2 * D[1] / 3)
mem.w8(RO.MODE_ADDR, RO.MODES["3band"])
until(TH)
theme(1)
until(TH + D[3] / 3)
theme(2)
until(TH + 2 * D[3] / 3)
theme(3)
until(BJ - 0.25)
theme(0)
tap((718, 38))                           # PERFORM
tap(TAB(1))                              # BEAT JUMP 1-2-4
tap(TAB(1))                              # 4-8-16
until(BJ + D[4] / 3 - 0.15)
tap(pad(4))                              # << 16 (fires on release)
until(BJ + 2 * D[4] / 3 - 0.15)
tap(pad(5))                              # >> 16
until(KS - 0.15)
tap(TAB(2))                              # KEY SHIFT
tap((551, 264))
tap((551, 264))                          # +2
until(KS + D[5] / 2 - 0.3)
for _ in range(4):
    tap((181, 264))                      # down to -2
until(ST - 0.15)
tap((365, 264))                          # RESET
tap(TAB(3))                              # STEMS
until(ST + D[6] / 2 - 0.15)
x, w = PP.STEMS5[0]
tap((x + 50, PP.HC_Y + PP.HC_H // 2))    # VOCALS off
until(MORE - 0.15)
tap(TAB(0))                              # HOT CUE
until(REC1)
a = np.array(dac, dtype=np.uint32).view(np.int32)
a = (a[: len(a) // 2 * 2].reshape(-1, 2) >> 16).astype(np.int16)
np.save("build/promo_rec_audio.npy", a)
json.dump({"fps": FPS, "rec0": REC0, "frames": state}, open("build/promo_rec.json", "w"))
print("frames", len(frames), "blk", state[0]["blk"], "->", state[-1]["blk"])
