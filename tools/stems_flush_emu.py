#!/usr/bin/env python3
"""stems_flush_emu.py -- EMULATOR ONLY: does a beat jump make the deck drop
its ~30 s read-ahead and re-read a STEMS file (so a mute is heard at once)?

    STICK=<folder> .venv/bin/python tools/stems_flush_emu.py prep
    .venv/bin/python tools/stems_flush_emu.py run ctl|mute|jump|mutejump
    .venv/bin/python tools/stems_flush_emu.py report

prep: user library snapshot + all mods (as the emulator app installs them)
      -> STICK swapped in (a copy of the library whose "Don't Come Back"
      file IS a STEMS-4 file, full length) -> BROWSE > ARTIST row 28 > load
      -> plays -> 25 s of audio so the read-ahead is full -> PERFORM > BEAT
      JUMP tab -> cache/emu_flush_playing.pkl
run:  from that snapshot, SECS of audio with one change at t=0:
      ctl nothing / mute VOCALS off / jump tap <<1 then >>1 / mutejump both.
report: first audible difference mute-vs-ctl and mutejump-vs-jump.
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

SNAP0 = os.environ.get("SNAP0", "cache/user_stick_mounted.pkl")
SNAP = os.environ.get("SNAP", "cache/emu_flush_playing.pkl")
FS = 44100


def mono(words):
    a = np.array(words, dtype=np.uint32).view(np.int32)
    a = a[: len(a) // 2 * 2].reshape(-1, 2)
    return (a >> 16).astype(np.float64).mean(1)


def first_change(a, b, blk=2048):
    n = min(len(a), len(b))
    for i in range(0, n - blk, blk):
        ea = np.abs(a[i:i + blk]).mean()
        diff = np.abs(a[i:i + blk] - b[i:i + blk]).mean()
        if diff > 0.25 * max(ea, 1.0):
            return i
    return None


class Deck:
    def __init__(self, path):
        self.m = Machine.load(path)
        self.mem, self.dev = self.m.mem, self.m.mem.dev
        self.d = self.dev.dspemu
        self.out, self.n0 = [], len(self.d.asp1.out)

    def run(self, n, frame=None):
        self.dev.panel_frame = frame or panl.build_frame()
        left = int(n)
        while left > 0:
            k = min(left, 50_000_000)
            self.m.cpu.run(limit=k)
            left -= k
            self.out += self.d.asp1.out[self.n0:]
            self.n0 = len(self.d.asp1.out)

    def key(self, k, n=80e6):
        self.run(5e6, panl.build_frame(keys=[k]))
        self.run(n)

    def touch(self, xy, n=40e6):
        self.run(4e6, panl.build_frame(touch=xy))
        self.run(n)

    def state(self):
        return self.d.bus.read(0x11837BF8, 4)


def prep():
    from emu import ui
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    import perform_pads as PP
    t0 = time.time()
    k = Deck(SNAP0)
    app = ui.EmuApp.__new__(ui.EmuApp)
    app.machine, app.fw_name, app.status = k.m, "Stock v1.15", ""
    app.apply_wave("rgb")
    u = k.dev._usb()
    u.unplug()
    k.run(20e6)
    disk = open_disk(os.environ["STICK"])
    k.dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(8):
        k.run(250e6)
    print("stick", u.stick.addr, f"[{time.time()-t0:.0f}s]", flush=True)
    k.key("DevUSB", 150e6)
    k.key("RotaryPush")
    for _ in range(int(os.environ.get("ART_ROW", "28"))):
        k.mem.w16(panl.ROTARY_COUNTER, (k.mem.r16(panl.ROTARY_COUNTER) + 1) & 0xFFFF)
        k.run(25e6)
    k.key("RotaryPush")
    k.key("RotaryPush", 300e6)
    for _ in range(20):
        k.run(200e6)
        if k.state() == 2:
            break
    print("dsp state", k.state(), "err", k.d.error, f"[{time.time()-t0:.0f}s]", flush=True)
    from dsp_pitch_probe import SH_STEPS_PER_S
    k.run(25 * SH_STEPS_PER_S)          # let the read-ahead fill
    k.touch((718, 38))                  # PERFORM
    k.touch((PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[1] + PP.TAB_H // 2))   # BEAT JUMP
    print("PERFORM mode", k.mem.r8(PP.PST + 2), "page", k.mem.r8(PP.PST + 1),
          "dsp", k.state(), f"[{time.time()-t0:.0f}s]", flush=True)
    k.m.save(SNAP)
    print("saved", SNAP)


def run(mode, secs=float(os.environ.get("SECS", "40"))):
    import perform_pads as PP
    from dsp_pitch_probe import SH_STEPS_PER_S
    k = Deck(SNAP)
    t0 = time.time()
    calls = []
    if os.environ.get("NEWSM"):         # swap in the current build/stems sm
        syms = {}
        for line in open("build/stems/sm.syms"):
            a, _, nm = line.split()
            syms[nm.lstrip("_")] = int(a, 16)
        img = open("build/stems/sm.bin", "rb").read()
        old_prev, old_entry = int(os.environ.get("OLDPREV", "0x09836c74"), 16), \
            int(os.environ.get("OLDENTRY", "0x0983620c"), 16)
        prev = k.mem.r32(old_prev)
        for a in range(syms["sm_table"], syms["end"], 4):
            k.mem.w32(a, 0)
        for i, by in enumerate(img):
            k.mem.w8(syms["sm_table"] + i, by)
        k.mem.w32(syms["sm_prev"], prev)
        for slot in (0x08D5FFA8, 0x08DDFFD4):
            if k.mem.r32(slot) == old_entry:
                k.mem.w32(slot, syms["sm_fread"])
        # mods chained IN FRONT of sm (FLAC/Opus layer) keep sm's entry as
        # their "prev": repoint those words too
        for a in range(0x09970000, 0x09A00000, 4):
            if k.mem.r32(a) == old_entry:
                k.mem.w32(a, syms["sm_fread"])
                print("repointed chained prev at", hex(a), flush=True)
        # later mods chained IN FRONT of sm keep sm's entry as their "prev"
        # (FLAC/Opus layer): repoint those words too
        for a in range(0x09970000, 0x09A00000, 4):
            if k.mem.r32(a) == old_entry:
                k.mem.w32(a, syms["sm_fread"])
                print("repointed chained prev at", hex(a), flush=True)
        k.syms = syms
        print("new sm installed, prev", hex(prev), flush=True)
    if mode in ("mute", "mutejump"):
        k.mem.w8(PP.PMASK, 0x01)        # VOCALS off (what the pad tap writes)
    if mode in ("jump", "mutejump"):
        for i in (0, 1):                # <<1 then >>1
            x, w = PP.ITEMS[i]
            k.touch((x + w // 2, PP.HC_Y + PP.HC_H // 2), 20e6)
        calls.append((k.mem.r32(PP.ROWREV & 0x1FFFFFFF), k.mem.r32(PP.ROWFWD & 0x1FFFFFFF)))
    if mode == "unmute":                # muted at t0, all back on at 3 s
        k.mem.w8(PP.PMASK, 0x01)
        k.run(3 * SH_STEPS_PER_S)
        k.mem.w8(PP.PMASK, 0x00)
    k.run(secs * SH_STEPS_PER_S - len(k.out) / 2 / FS * SH_STEPS_PER_S)
    a = mono(k.out)
    np.save(f"build/flush_{mode}{os.environ.get('TAG', '')}.npy", a)
    if hasattr(k, "syms"):
        print("calls", k.mem.r32(k.syms["sm_calls"]), "valid", k.mem.r32(k.syms["sm_valid"]), "hits", k.mem.r32(k.syms["sm_hits"]), "lastpos", k.mem.r32(k.syms["sm_lastpos"]))
        print("dr blocks", k.mem.r32(k.syms["sm_drblk"]), "scans", k.mem.r32(k.syms["sm_drscan"]),
              "skips", k.mem.r32(k.syms["sm_drskip"]), "play", k.d.bus.read(0x11837CCC, 4), flush=True)
    print(f"{mode}: {len(a)/FS:.1f} s audio, dsp {k.state()} err {k.d.error} "
          f"rows {calls} [{time.time()-t0:.0f}s]", flush=True)


def report():
    r = {m: np.load(f"build/flush_{m}.npy") for m in ("ctl", "mute", "jump", "mutejump")
         if os.path.exists(f"build/flush_{m}.npy")}
    for a, b in (("ctl", "mute"), ("jump", "mutejump"), ("ctl", "jump")):
        if a in r and b in r:
            i = first_change(r[a], r[b])
            print(f"{b} vs {a}:", "no change" if i is None else f"differs after {i/FS:.2f} s")


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "prep":
        prep()
    elif c == "run":
        run(sys.argv[2])
    else:
        report()
