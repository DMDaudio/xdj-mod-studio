#!/usr/bin/env python3
"""stems_delay_emu.py -- EMULATOR ONLY, real DSP model: how long after a
PERFORM tap does the change reach the audio output?

    .venv/bin/python tools/stems_delay_emu.py prep          (once, ~5 min)
    .venv/bin/python tools/stems_delay_emu.py measure [stems|key]

prep:    cache/emu_dsp_mounted6.pkl (real DSP, no track) -> the emulator
         app's own mod install (emu/ui.py: pads, versions, stems, key
         shift) -> stick = cache/stemsdelay_stick (stick7's analysed tracks,
         "Control clip.wav" rebuilt as a STEMS-4 file: vocals = the clip,
         drums/bass/other silent) -> browse + load the clip -> PLAY with
         MASTER TEMPO on -> cache/emu_stemsdelay_playing.pkl
measure: from that snapshot, two runs with identical input except one
         change at t=0: `stems` = VOCALS muted (PMASK bit 0; the output must
         go silent), `key` = +2 semitones (the key word the PERFORM KEY
         SHIFT tab writes). The emulator is deterministic, so the first DAC
         block where the runs differ = the delay, in audio time.
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

SNAP0 = "cache/emu_dsp_mounted6.pkl"
SNAP1 = "cache/emu_stemsdelay_mounted.pkl"
SNAP2 = os.environ.get("SNAP2", "cache/emu_stemsdelay_playing.pkl")
STICK = os.environ.get("STICK", "cache/stemsdelay_stick")
ROT = panl.ROTARY_COUNTER
FS = 44100


def install_mods(m):
    """exactly what the emulator app's Waveform menu installs"""
    from emu import ui
    import perform_pads as PP
    import vredirect as VR
    if all(m.mem.r32(p) == old for p, old, new in PP.hooks()):
        PP.install(m.mem)
    if m.mem.r32(VR.HOOK) == VR.FREAD:
        for a, b in VR.plan():
            m.mem.w8(a, b)
    ui.EmuApp._install_stems(None, m)
    ui.EmuApp._install_keyshift(None, m)


def shot(m, name):
    from ghosttap_emu import fb_png
    os.makedirs("screenshots/stemsdelay", exist_ok=True)
    fb_png(m, f"screenshots/stemsdelay/{name}.png")


def prep():
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    t0 = time.time()
    m = Machine.load(SNAP0)
    mem, dev, d = m.mem, m.mem.dev, m.mem.dev.dspemu
    run = lambda n: m.cpu.run(limit=int(n))   # noqa: E731

    def key(k, n=15e6):
        dev.panel_frame = panl.build_frame(keys=[k])
        run(3e6)
        dev.panel_frame = panl.build_frame()
        run(n)
    install_mods(m)
    run(30e6)
    ks = {}
    for line in open("build/keyshift/ksh.syms"):
        a, _, n = line.split()
        ks[n.lstrip("_")] = int(a, 16)
    print(f"key shift cave status {mem.r32(ks['ksh_status'])} (1 = live), DSP call "
          f"{d.bus.read(0xC0008D64, 4):#x}, stems hook {mem.r32(0x08D5FFA8):#x}", flush=True)
    d.ratio = 0.1
    u = dev._usb()
    u.unplug()
    run(20e6)
    disk = open_disk(STICK)
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(8):
        run(250e6)
    print(f"stick addr {u.stick.addr} [{time.time()-t0:.0f}s]", flush=True)
    m.save(SNAP1)
    key("DevUSB", 120e6)
    shot(m, "browse0")
    print("saved", SNAP1, "- now pick the row: browse NAV='push,rot+,...'", flush=True)


def nav(seq):
    m = Machine.load(SNAP1)
    mem, dev, d = m.mem, m.mem.dev, m.mem.dev.dspemu
    run = lambda n: m.cpu.run(limit=int(n))   # noqa: E731

    def key(k, n=80e6):
        dev.panel_frame = panl.build_frame(keys=[k])
        run(3e6)
        dev.panel_frame = panl.build_frame()
        run(n)
    d.ratio = 0.1
    key("DevUSB", 120e6)
    for i, s in enumerate(seq):
        if s == "push":
            key("RotaryPush")
        elif s in ("rot+", "rot-"):
            mem.w16(ROT, (mem.r16(ROT) + (1 if s == "rot+" else -1)) & 0xFFFF)
            run(30e6)
        elif s == "load":
            from emu.c6x.dspdev import DSP_PER_SH
            d.ratio = DSP_PER_SH
            key("RotaryPush", 300e6)
        shot(m, f"nav{i}_{s}")
    return m


def play(seq):
    t0 = time.time()
    m = nav(seq + ["load"])
    mem, dev, d = m.mem, m.mem.dev, m.mem.dev.dspemu
    run = lambda n: m.cpu.run(limit=int(n))   # noqa: E731
    print(f"loaded: track len {mem.r32(0x0BA3C050)} mode {mem.r8(0x0B6C4264)} "
          f"err {d.error} [{time.time()-t0:.0f}s]", flush=True)
    dev.panel_frame = panl.build_frame(keys=["MasterTempo"], tempo=250)
    run(5e6)
    dev.panel_frame = panl.build_frame(tempo=250)
    run(30e6)
    # Is the DSP playing? Ask it (status 0x11837BF8: 2 playing, 3 paused).
    # NOT the mode word: in this line a load auto-plays at mode 3 and the
    # old `not mode & 4 -> press PLAY` PAUSED the track (mode 5) -- the
    # paused DSP loops its output ring, the "machine gun" (2026-10-18).
    for _ in range(20):
        if d.bus.read(0x11837BF8, 4) == 2:
            break
        run(25e6)
    if d.bus.read(0x11837BF8, 4) != 2:
        dev.panel_frame = panl.build_frame(keys=("PlayPause",), tempo=250)
        run(3e6)
        dev.panel_frame = panl.build_frame(tempo=250)
    n0 = len(d.asp1.out)
    for _ in range(10):
        run(100e6)
    out = np.array(d.asp1.out[n0:], dtype=np.uint32)
    print(f"playing: mode {mem.r8(0x0B6C4264)} DSP state {d.bus.read(0x11837BF8, 4)} "
          f"(2 = playing) MT {mem.r8(0x0B6C4308)} DAC words "
          f"{len(out)} nonzero {int((out != 0).sum())} err {d.error} "
          f"[{time.time()-t0:.0f}s]", flush=True)
    shot(m, "playing")
    m.save(SNAP2)
    print("saved", SNAP2)


def mono(words):
    """DAC words alternate L, R; the sample is the top 16 bits
    (dsp_pitch_probe.wav_write / emu/dspsound.py)"""
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


def measure(whats, secs=float(os.environ.get("SECS", "45"))):
    import perform_pads as PP
    import dsp_pitch_probe as P
    runs = {}
    for mode in ["ctl"] + whats:
        t = time.time()
        m = Machine.load(SNAP2)
        mem, d = m.mem, m.mem.dev.dspemu
        m.mem.dev.panel_frame = panl.build_frame(tempo=250)
        n0 = len(d.asp1.out)
        if mode == "stems":
            mem.w8(PP.PMASK, 0x01)                  # VOCALS muted (pad tap)
        elif mode == "key":
            mem.w32(0x09D24DD8, 0x40000000 | int(round(2 ** (2 / 12) * (1 << 20))))
        out = []
        steps = int(secs * P.SH_STEPS_PER_S)
        while steps > 0:
            k = min(steps, 100_000_000)
            m.cpu.run(limit=k)
            steps -= k
            out += d.asp1.out[n0:]
            n0 = len(d.asp1.out)               # (asp1.out is bounded)
        runs[mode] = mono(out)
        np.save(f"build/stemsdelay_{mode}.npy", runs[mode])
        print(f"{mode}: {len(runs[mode]) / FS:.1f} s audio, err {d.error} "
              f"[{time.time()-t:.0f}s]", flush=True)
    for w in whats:
        i = first_change(runs["ctl"], runs[w])
        if i is None:
            print(f"RESULT {w}: no audible change within {len(runs[w]) / FS:.1f} s")
        else:
            lv = np.abs(runs[w][i:i + FS]).mean()
            lc = np.abs(runs["ctl"][i:i + FS]).mean()
            print(f"RESULT {w}: change heard after {i / FS:.2f} s "
                  f"(level {lv:.0f} vs control {lc:.0f})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prep"
    if cmd == "prep":
        prep()
    elif cmd == "nav":
        nav(sys.argv[2].split(","))
    elif cmd == "play":
        play(sys.argv[2].split(","))
    else:
        measure(sys.argv[2].split(",") if len(sys.argv) > 2 else ["stems", "key"])
