#!/usr/bin/env python3
"""brake_probe.py -- VINYL BRAKE stop on the REAL DSP program (emulator,
no deck): record the audio of a pause and measure how long the slow-down
lasts.

From a playing snapshot (default cache/emu_dsp_playing7.pkl) it optionally
taps the on-screen VINYL BRAKE button (touch zone 8, centre 52,367), plays
PRE seconds, presses PLAY/PAUSE and records POST seconds of DAC output.
While recording it samples, every SAMPLE SH steps, the SH->DSP command
block (0x11837BC0..+0x40) and the DSP state block around b14
(0x11806800..0x11806E00), so the words that RAMP can be found.

Measured:
  stop_ms   time from the pause press until the output envelope falls
            below -40 dB of the pre-pause level (20 ms RMS windows)
  ramp_ms   time over which a sampled DSP float word moves from its
            playing value to 0 (the speed ramp), when one is found
Env: AGAIN=1 (then PLAY again: vinyl-start ramp time)  BRAKE=0|1 (default 1)  PRE=1.0  POST=4.0  SAMPLE=5e6
     OUT=build/brake.wav  JSON=build/brake_probe.json
     SH16=addr=val[,..] SH u16 RAM writes after load (brake length setting:
               SH16=0x09D24692=6500 -> the length formula, see CLAUDE.md)
     SH32=addr=val[,..] SH u32 RAM writes after load (brake length constant:
               SH32=0x08D3714C=12e6 = 2x the stock 6e6 -> 2x longer)
     DSPPATCH=addr=val[,..] DSP words written (int, or float with a '.')
               right after load (and re-checked before the pause)
"""
import json
import os
import struct
import sys
import time
import wave
sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

SNAP = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_playing7.pkl"
FS = 44100
STEPS_S = 500e6
BRAKE = os.environ.get("BRAKE", "1") == "1"
PRE = float(os.environ.get("PRE", "1.0"))
POST = float(os.environ.get("POST", "4.0"))
SAMPLE = int(float(os.environ.get("SAMPLE", "5e6")))
OUT = os.environ.get("OUT", "build/brake.wav")
JSONF = os.environ.get("JSON", "build/brake_probe.json")
VB = (52, 367)
MBOX = (0x11837BC0, 0x40)
STATE = (0x11806800, 0x600)


def parse_patch():
    out = {}
    for kv in filter(None, os.environ.get("DSPPATCH", "").split(",")):
        a, v = kv.split("=")
        if "." in v:
            out[int(a, 16)] = struct.unpack("<I", struct.pack("<f", float(v)))[0]
        else:
            out[int(a, 16)] = int(v, 0) & 0xFFFFFFFF
    return out


def words(d, base, n):
    return [d.bus.read(base + i, 4) for i in range(0, n, 4)]


def envelope(x, win=882):
    k = len(x) // win
    return np.sqrt((x[:k * win].reshape(k, win) ** 2).mean(1) + 1e-9)


def main():
    m = Machine.load(SNAP)
    dev, mem = m.mem.dev, m.mem
    d = dev.dspemu
    assert d is not None, "snapshot has no real DSP"
    patch = parse_patch()
    for a, v in patch.items():
        d.bus.write(a, 4, v)
    sh16 = {int(a, 16): int(v, 0) & 0xFFFF for a, v in
            (kv.split("=") for kv in filter(None, os.environ.get("SH16", "").split(",")))}
    for a, v in sh16.items():
        mem.w16(a, v)
    sh32 = {int(a, 16): int(float(v)) & 0xFFFFFFFF for a, v in
            (kv.split("=") for kv in filter(None, os.environ.get("SH32", "").split(",")))}
    for a, v in sh32.items():
        mem.w32(a, v)
    print(f"brake setting u16 [0x09D24692] = {mem.r16(0x09D24692) & 0xFFFF}")

    def run(s):
        m.cpu.run(limit=int(s * STEPS_S))

    def press(key=None, touch=None, hold=0.15, after=0.3):
        dev.panel_frame = panl.build_frame(keys=(key,) if key else (), touch=touch)
        run(hold)
        dev.panel_frame = panl.build_frame()
        run(after)

    print(f"mode {mem.r8(0x0B6C4264)} VB light {mem.r8(0x0C0CCBA4)}")
    if os.environ.get("START") == "1":               # snapshot paused: start first
        press("PlayPause")
        run(1.0)
        print(f"started: mode {mem.r8(0x0B6C4264)}")
    if BRAKE:
        press(touch=VB)
        print(f"VB tapped: light {mem.r8(0x0C0CCBA4)} byte 0x087F4000={mem.r8(0x087F4000):#x}")
    run(PRE)
    for a, v in patch.items():
        if d.bus.read(a, 4) != v:
            print(f"  NOTE patch {a:#x} now {d.bus.read(a, 4):#x} (was set {v:#x})")
    for a, v in sh16.items():
        if mem.r16(a) & 0xFFFF != v:
            print(f"  NOTE SH {a:#x} was rewritten to {mem.r16(a) & 0xFFFF} -> set {v} again")
        mem.w16(a, v)
    n0 = len(d.asp1.out)
    mode0 = mem.r8(0x0B6C4264)
    samples = []
    t = time.time()
    dev.panel_frame = panl.build_frame(keys=("PlayPause",))
    steps, total, released = 0, int(POST * STEPS_S), False
    while steps < total:
        if not released and steps >= 0.15 * STEPS_S:
            dev.panel_frame = panl.build_frame()
            released = True
        m.cpu.run(limit=SAMPLE)
        steps += SAMPLE
        samples.append((len(d.asp1.out) - n0, mem.r8(0x0B6C4264),
                        words(d, *MBOX), words(d, *STATE), mem.r32(0x0B6C2C50)))
    out = d.asp1.out[n0:]
    a = np.array(out, dtype=np.uint32).view(np.int32)
    a = a[: len(a) // 2 * 2].reshape(-1, 2)
    pcm = (a >> 16).astype("<i2")
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with wave.open(OUT, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(pcm.tobytes())
    x = pcm.astype(float).mean(1)
    env = envelope(x)
    ref = np.median(env[:5]) if len(env) >= 5 else env.max()
    below = np.where(env < ref * 0.01)[0]
    stop_ms = float(below[0] * 20) if len(below) else None
    # SH speed word 0x0B6C2C50 (Q20): sampled every SAMPLE steps
    q = [s[4] for s in samples]
    start_q = q[0]
    # ramp = how long the speed word the DSP actually receives (mailbox
    # BC0) stays non-zero after the pause press (the SH word itself shows
    # one-tick scratch zeros mid-ramp that never reach the mailbox)
    b = [s[2][0] for s in samples]
    nz = [i for i, v in enumerate(b) if v]
    ramp_ms = round((nz[-1] + 1) * SAMPLE / STEPS_S * 1000, 1) if nz and nz[-1] < len(b) - 1 else None
    start_ms = None
    if os.environ.get("AGAIN") == "1":       # PLAY again: vinyl start ramp
        dev.panel_frame = panl.build_frame(keys=("PlayPause",))
        up, steps = [], 0
        while steps < int(1.5 * STEPS_S):
            if steps >= 0.15 * STEPS_S:
                dev.panel_frame = panl.build_frame()
            m.cpu.run(limit=SAMPLE)
            steps += SAMPLE
            up.append(d.bus.read(MBOX[0], 4))
        full = next((i for i, v in enumerate(up) if v >= 0x100000), None)
        start_ms = None if full is None else round((full + 1) * SAMPLE / STEPS_S * 1000, 1)
    # words that ramp: float-looking state words that change, end at 0
    ramps = []
    st = np.array([s[3] for s in samples], dtype=np.uint32)
    fr = [s[0] for s in samples]
    for i in range(st.shape[1]):
        col = st[:, i]
        if len(set(col.tolist())) < 4:
            continue
        f = col.view(np.float32)
        if not np.all(np.isfinite(f)) or np.abs(f).max() > 1e6:
            continue
        nz = np.where(np.abs(f) > 1e-6)[0]
        ramps.append({"addr": hex(STATE[0] + 4 * i), "first": float(f[0]),
                      "last": float(f[-1]), "distinct": len(set(col.tolist())),
                      "last_nonzero_frame": int(fr[nz[-1]]) if len(nz) else None,
                      "series": [round(float(v), 5) for v in f[:: max(1, len(f) // 40)]]})
    mb = np.array([s[2] for s in samples], dtype=np.uint32)
    mbch = {hex(MBOX[0] + 4 * i): sorted({hex(v) for v in mb[:, i].tolist()})[:8]
            for i in range(mb.shape[1]) if len(set(mb[:, i].tolist())) > 1}
    res = {"brake": BRAKE, "patch": {hex(k): hex(v) for k, v in patch.items()},
           "mode_before": mode0, "mode_after": mem.r8(0x0B6C4264),
           "frames": len(x), "stop_ms": stop_ms, "ramp_ms": ramp_ms, "start_ms": start_ms,
           "sh16": {hex(k): v for k, v in sh16.items()},
           "sh32": {hex(k): v for k, v in sh32.items()},
           "speed_series": q[:200], "env_ref": float(ref),
           "env_20ms": [round(float(e), 1) for e in env[:200]],
           "mailbox_changes": mbch,
           "bc0_series": [[int(s[0]), int(s[1]), int(s[2][0])] for s in samples], "ramping_words": ramps}
    with open(JSONF, "w") as f:
        json.dump(res, f, indent=1)
    print(f"brake={BRAKE} mode {mode0}->{res['mode_after']} output {len(x) / FS:.2f}s "
          f"RAMP {ramp_ms} ms (mailbox speed) START {start_ms} ms STOP {stop_ms} ms (env ref {ref:.0f}) [{time.time() - t:.0f}s] -> {OUT}")
    print("mailbox changes:", mbch)
    for r in ramps[:30]:
        print(f"  {r['addr']}: {r['first']:.5g} -> {r['last']:.5g} "
              f"distinct {r['distinct']} last_nonzero_frame {r['last_nonzero_frame']}")


if __name__ == "__main__":
    main()
