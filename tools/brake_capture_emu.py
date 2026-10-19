#!/usr/bin/env python3
"""brake_capture_emu.py -- what does the main CPU do during a VINYL BRAKE
stop? (emulator, HLE DSP, no deck)

Two runs from the track-loaded snapshot: PLAY 2 s, then PAUSE, logging
every word MAIN writes to the DSP mailbox (with emulated time) for 3 s
after the pause -- once with VINYL BRAKE off, once with it tapped on
(on-screen button, native touch). If the brake is a ramp computed on the
SH side, the "on" run shows a stream of decreasing speed words after the
pause; if the SH only sends a "brake" command, it shows one extra write.

Run: .venv/bin/python tools/brake_capture_emu.py [ckpt]
"""
import os
import sys

os.environ["XDJ_HLE_DSP"] = "1"
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu import panl                     # noqa: E402

CPU = 500_000_000
CKPT = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_track_loaded.pkl"
VB = (43, 308)                           # VINYL BRAKE button (screen px)


def session(brake):
    m = Machine.load(CKPT)
    dev = m.mem.dev
    hle = dev.hle
    log = []
    orig = hle.write
    t = [0]

    def wr(a, size, val):
        before = dict(hle.echo)
        orig(a, size, val)
        for k, v in hle.echo.items():
            if before.get(k) != v:
                log.append((m.cpu.cyc if hasattr(m.cpu, "cyc") else t[0], k, v))
    hle.write = wr

    def run(s):
        m.cpu.run(limit=int(s * CPU))
        t[0] += int(s * CPU)

    def press(key=None, touch=None, hold=0.12):
        dev.panel_frame = panl.build_frame(keys=(key,) if key else (),
                                           touch=touch, tempo=250)
        run(hold)
        dev.panel_frame = panl.build_frame(tempo=250)
        run(0.4)

    if brake:
        press(touch=VB)
    press("PlayPause")
    run(2.0)
    mode = m.mem.r8(0x0B6C4264)
    log.clear()
    t0 = t[0]
    press("PlayPause")
    run(3.0)
    return m, mode, [(x, k, v) for x, k, v in log]


def main():
    res = {}
    for brake in (False, True):
        m, mode, log = session(brake)
        per = {}
        for _, k, v in log:
            per.setdefault(k, []).append(v)
        res[brake] = per
        print(f"--- brake={'ON' if brake else 'off'}: mode while playing={mode}, "
              f"after pause={m.mem.r8(0x0B6C4264)}, {len(log)} mailbox writes")
        for k in sorted(per):
            vs = per[k]
            print(f"  {k:#010x}: {len(vs):4d} writes  first {vs[0]:#010x}  "
                  f"last {vs[-1]:#010x}  distinct {len(set(vs))}")
    only = sorted(set(res[True]) - set(res[False]))
    diff = [k for k in sorted(set(res[True]) & set(res[False]))
            if set(res[True][k]) != set(res[False][k])]
    print("addresses written only with brake:", [hex(k) for k in only])
    print("addresses with different values:", [hex(k) for k in diff])
    for k in only + diff:
        print(f"  {k:#x} brake values: {[hex(v) for v in res[True][k][:40]]}")


if __name__ == "__main__":
    main()
