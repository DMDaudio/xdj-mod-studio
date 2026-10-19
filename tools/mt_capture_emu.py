#!/usr/bin/env python3
"""mt_capture_emu.py -- the mt_capture experiment on the EMULATOR (no deck).

Same idea as tools/mt_capture.py, same RAM windows, same five states --
but the machine is the emulator booted from the track-loaded snapshot,
XDJ_HLE_DSP=1 serves the C6x mailbox so PLAY advances, and input goes
through panl.build_frame. What this answers:

  * When MASTER TEMPO engages, does the SH side write a stretch RATIO
    (Q20 @0x0B6C2C50 / DSP-ctx words near 0x09D24DC0) that is INDEPENDENT
    of the transport rate?  (=> real-time key shift is "write a ratio",
    plausibly one RAM hook away)
  * or is the ratio just 1/rate, hard-coupled to the fader? (=> MT cannot
    be repurposed for key shift)

Also dumps the DSP mailbox block (0x11837B80..0x11837C80) per state: the
same window is where deck-side decode / stems PCM handoff has to live.

States: idle (snapshot) -> playing -> mt-on -> mt-on-moved (fader +3%)
-> mt-off. Output: /tmp/mt_emu_<state>.log + a changed-words diff.

Run: .venv/bin/python tools/mt_capture_emu.py [--ckpt cache/emu_track_loaded.pkl]
"""
import os
import sys
import time

os.environ["XDJ_HLE_DSP"] = "1"          # before emu imports (SH7724 gate)
sys.path[:0] = [".", "tools"]

from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

CPUFREQ = 500_000_000
MODE_ADDR = 0x0B6C4264                   # 3 = paused/loaded, 5 = playing

# (label, addr, count) -- count in bytes; mirrors tools/mt_capture.py
WINDOWS = [
    ("mt area 0x0B6C4300 (4304 clr, 4308 MT state)", 0x0B6C4300, 16),
    ("tempo area 0x0B6C2C40 (2C48 dirty, 2C50 Q20 ratio)", 0x0B6C2C40, 32),
    ("rate area 0x0B6C3E10 (3E18 rate)", 0x0B6C3E10, 32),
    ("derived MT 0x0B6C3C10 (3C14 active flag)", 0x0B6C3C10, 16),
    ("dsp ctx 0x09D24D80..E40 (A84 rev, A85 MT, A88 ratio)", 0x09D24D80, 192),
    ("persist 0x0A4FD0E0 (0E8 bit6 = MT saved)", 0x0A4FD0E0, 16),
    ("mailbox 0x11837B80..C80 (cmd block/status)", 0x11837B80, 256),
]

SNAP = [None]


def ckpt_arg():
    for i, a in enumerate(sys.argv):
        if a == "--ckpt" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return "cache/emu_track_loaded.pkl"


def run(m, seconds):
    m.cpu.run(limit=int(seconds * CPUFREQ))


def frame(m, keys=(), fader=250):
    m.mem.dev.panel_frame = panl.build_frame(
        keys=keys, fader_raw=fader, jog=0, rotary=0, needle=0, touch=None,
        tempo=fader)


def press(m, key, fader, hold=0.12, settle=0.9):
    frame(m, (key,), fader)
    run(m, hold)
    frame(m, (), fader)
    run(m, settle)


def rd(m, addr, n):
    return bytes(m.mem.r8(addr + i) for i in range(n))


def words(buf, base, w=4):
    out = {}
    for off in range(0, len(buf) - w + 1, w):
        out[base + off] = int.from_bytes(buf[off:off + w], "little")
    return out


def dump(m, label, fader):
    mode = int.from_bytes(rd(m, MODE_ADDR, 4), "little")
    print(f"--- {label} | mode={mode} fader={fader} "
          f"t={time.strftime('%H:%M:%S')} ---", flush=True)
    snap = {"mode": mode}
    lines = []
    for name, addr, cnt in WINDOWS:
        buf = rd(m, addr, cnt)
        snap[addr] = buf
        lines.append(f"[{name}]")
        for off in range(0, cnt, 16):
            row = buf[off:off + 16]
            hexs = " ".join(f"{b:02x}" for b in row)
            words_ = " ".join(
                f"{int.from_bytes(row[i:i + 4], 'little'):08x}"
                for i in range(0, len(row) - 3, 4))
            lines.append(f"  {addr + off:08x}  {hexs:<48}  {words_}")
    open(f"/tmp/mt_emu_{label}.log", "w").write("\n".join(lines) + "\n")
    SNAP[0] = snap if SNAP[0] is None else SNAP[0]
    globals().setdefault("SNAPS", {})[label] = snap
    return snap


def diff(a, b):
    """Changed 32-bit words between two dumps (any window, aligned 4)."""
    out = []
    for name, addr, cnt in WINDOWS:
        wa, wb = words(SNAPS[a][addr], addr), words(SNAPS[b][addr], addr)
        for k in sorted(set(wa) | set(wb)):
            if wa.get(k) != wb.get(k):
                out.append((k, wa.get(k), wb.get(k)))
    return out


def main():
    t0 = time.time()
    m = Machine.load(ckpt_arg())
    print(f"snapshot loaded in {time.time() - t0:.0f}s; running states",
          flush=True)
    fader = 250                                   # ~0% tempo
    frame(m, (), fader)
    run(m, 1.0)
    dump(m, "idle", fader)

    press(m, "PlayPause", fader); run(m, 1.2)
    dump(m, "playing", fader)

    press(m, "MasterTempo", fader); run(m, 1.2)
    dump(m, "mt-on", fader)

    fader = 175                                   # ~ +3% tempo
    frame(m, (), fader); run(m, 1.5)
    dump(m, "mt-on-moved", fader)

    press(m, "MasterTempo", fader); run(m, 1.2)
    dump(m, "mt-off", fader)

    print("\n==== changed words between states (32-bit LE) ====")
    for a, b in (("idle", "playing"), ("playing", "mt-on"),
                 ("mt-on", "mt-on-moved"), ("mt-on-moved", "mt-off")):
        d = diff(a, b)
        print(f"\n-- {a} -> {b}: {len(d)} words")
        for k, va, vb in d[:40]:
            print(f"  {k:08x}: {va if va is None else f'{va:08x}'}"
                  f" -> {vb if vb is None else f'{vb:08x}'}")
    print("\nlogs: /tmp/mt_emu_*.log")


if __name__ == "__main__":
    main()
