#!/usr/bin/env python3
"""dsp_pitch_probe.py -- what the XDJ-700's real DSP program does with the
tempo/pitch words, measured on its own audio output (emulator, no deck).

Starts from a playing snapshot (default cache/emu_dsp_playing7.pkl: Get
Lucky ESQUIRE playing on the real DSP), optionally turns MASTER TEMPO on,
forces the SH->DSP command words, plays SECS seconds and measures:
  pitch : semitones vs the unchanged run (log-frequency spectrum shift)
  speed : REMAIN-clock seconds per output second
Env:
  MT=1            press MASTER TEMPO first
  BC0=1.12        force the tempo word   (SH 0x09D24DC8 -> mailbox BC0;
                  its store 0x08D36B34 NOPed, as tools/keyshift_v2_apply.py)
  BD8=1.12        force the second word  (SH 0x09D24DD8 -> mailbox BD8;
                  store 0x08D36B3A NOPed)
  SECS=4  OUT=build/pitch_probe.wav  REF=build/pitch_ref.wav
The first run without BC0/BD8 writes REF; later runs compare against it.
"""
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
SECS = float(os.environ.get("SECS", "4"))
OUT = os.environ.get("OUT", "build/pitch_probe.wav")
REF = os.environ.get("REF", "build/pitch_ref.wav")
FS = 44100
SH_STEPS_PER_S = 500e6                   # SH7724 at 500 MHz (DSP ratio 0.6)


def q20(x):
    return int(round(x * (1 << 20))) & 0xFFFFFFFF


def remain_ms(m):
    """REMAIN clock in ms from the SH position word (75 Hz frames)"""
    return m.mem.r32(0x09D246E0)


def wav_write(path, words):
    a = np.array(words, dtype=np.uint32).view(np.int32)
    a = a[: len(a) // 2 * 2].reshape(-1, 2)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((a >> 16).astype("<i2").tobytes())
    return (a >> 16).astype(float).mean(1)


def logspec(x):
    """average magnitude spectrum on a 1/48-octave log-frequency grid
    (150..6000 Hz), interpolated so every grid point has a value"""
    n = 16384
    win = np.hanning(n)
    acc = np.zeros(n // 2 + 1)
    for s in range(0, len(x) - n, n // 4):
        acc += np.abs(np.fft.rfft(x[s:s + n] * win))
    f = np.fft.rfftfreq(n, 1 / FS)
    grid = 150 * 2 ** (np.arange(0, 48 * np.log2(6000 / 150)) / 48)
    return np.log1p(np.interp(grid, f, acc))


def pitch_shift(x, ref):
    """semitones x is above ref: the log-spectrum shift with the best
    Pearson correlation over the overlap (+-8 semitones, 1/4-st steps)"""
    a, b = logspec(x), logspec(ref)
    best, bs = -2.0, 0
    for s in range(-32, 33):
        if s >= 0:
            u, v = a[s:], b[:len(b) - s]
        else:
            u, v = a[:s], b[-s:]
        c = np.corrcoef(u, v)[0, 1]
        if c > best:
            best, bs = c, s
    return bs / 4.0


def beat_period(x):
    """beat period in samples from the onset envelope's autocorrelation"""
    h = 441
    e = np.array([np.sum(x[i:i + h] ** 2) for i in range(0, len(x) - h, h)])
    o = np.maximum(np.diff(np.log1p(e)), 0)
    o -= o.mean()
    r = np.correlate(o, o, "full")[len(o) - 1:]
    lo, hi = int(0.30 * FS / h), int(0.75 * FS / h)
    k = lo + int(np.argmax(r[lo:hi]))
    # refine with parabola
    if 0 < k < len(r) - 1:
        a, b, c = r[k - 1], r[k], r[k + 1]
        k = k + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
    return k * h


def main():
    m = Machine.load(SNAP)
    d = m.mem.dev.dspemu
    dev = m.mem.dev
    mem = m.mem
    if os.environ.get("MT"):
        dev.panel_frame = panl.build_frame(keys=["MasterTempo"])
        m.cpu.run(limit=5_000_000)
        dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=30_000_000)
        print("MT word 0x0B6C4308 =", mem.r8(0x0B6C4308), "BC4 =", hex(d.bus.read(0x11837BC4, 4)))
    forced = {}
    if "BC0" in os.environ:
        mem.w16(0x08D36B34, 0x0009)
        forced[0x09D24DC8] = q20(float(os.environ["BC0"]))
    if "BD8" in os.environ:
        mem.w16(0x08D36B3A, 0x0009)
        forced[0x09D24DD8] = q20(float(os.environ["BD8"]))
    dspf = {}
    for kv in filter(None, os.environ.get("DSPF", "").split(",")):
        a, v = kv.split("=")
        dspf[int(a, 16)] = struct.unpack("<I", struct.pack("<f", float(v)))[0]
    for kv in filter(None, os.environ.get("DSPI", "").split(",")):
        a, v = kv.split("=")
        dspf[int(a, 16)] = int(v, 0) & 0xFFFFFFFF
    chunk = int(float(os.environ.get("CHUNK", "100e6")))

    def force():
        for a, v in forced.items():
            mem.w32(a, v)
        for a, v in dspf.items():
            d.bus.write(a, 4, v)
    force()
    for _ in range(int(60e6 // chunk) or 1):           # let the change settle
        force()
        m.cpu.run(limit=chunk)
    n0 = len(d.asp1.out)
    p0 = remain_ms(m)
    t = time.time()
    steps = int(SECS * SH_STEPS_PER_S)
    while steps > 0:
        k = min(steps, chunk)
        force()
        m.cpu.run(limit=k)
        steps -= k
    words = d.asp1.out[n0:]
    p1 = remain_ms(m)
    x = wav_write(OUT, words)
    out_s = len(x) / FS
    print(f"mailbox BC0 {d.bus.read(0x11837BC0, 4):#x} BD8 {d.bus.read(0x11837BD8, 4):#x} "
          f"BC4 {d.bus.read(0x11837BC4, 4):#x}; output {out_s:.2f} s, position word "
          f"{p0} -> {p1} ({(p1 - p0) / 75:.2f} s at 75/s) [{time.time() - t:.0f}s]")
    if not forced and not dspf and not os.path.exists(REF):
        wav_write(REF, words)
        print("reference written", REF)
    elif os.path.exists(REF):
        with wave.open(REF) as w:
            r = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(float).mean(1)
        bp, br = beat_period(x), beat_period(r)
        print(f"PITCH vs reference: {pitch_shift(x, r):+.2f} semitones; "
              f"TEMPO vs reference: {br / bp:.3f} x  (BPM {60 * FS / bp:.1f} vs {60 * FS / br:.1f})")


if __name__ == "__main__":
    main()
