#!/usr/bin/env python3
"""gatedcue_emu.py -- emulator proof for the gated hot cue (tools/gatedcue.py).

Proof strategy (emu limits stated honestly):
  * The emu has no DSP/audio: position never advances, TIME --:--, and the
    session track carries no hot-cue points, so a stock hot-cue press from
    pause does NOT start playing here (mode stays 3).  On deck it would
    (jump + play).  The gated RELEASE (pause at cue) is therefore proven
    with a stock-driven 3 -> 5 substitution: after arming from pause we
    drive 3 -> 5 with the stock panel PlayPause (the same consumer the
    stock hot cue would drive), then release the pad and show the cave
    holds PlayPause through the stock panel path until mode returns 5->3.
  * Observables are firmware state, not audio: play word *0x0B6C4264
    (3 paused / 5 playing), zone state *0x0C0E75BC (0x1E idle / 0x17 A
    held), key byte 86 bit 0x40 (stock A handler fired), and the cave
    bytes GLATCH/GPRIOR/GPEND.  Mode flips are the firmware's own
    play/pause state -- the gated effect -- even though no PCM flows.

Subtests (each from a fresh load to isolate the known emu panel-stall):
  OFF  playing -> press A -> release: latch never arms, mode stays 5.
  ON   playing -> press A -> release: latch 1/prior 1 while held, mode
       stays 5 after release (prior play resumes = stock latch).
  ON   paused -> press A (arm, prior 0) -> stock PlayPause to 5 while
       still armed -> release: pause-pending holds PlayPause, mode 5->3,
       pending clears.  This is the gated preview-while-held.
  ON   paused -> press A -> release with no play (empty cue): safe
       no-op, mode stays 3, no pending stuck.
  ON   press outside A/B/C: never arms, pure stock.

  gatedcue_emu.py run   (prints PASS/FAIL per check + RESULT)
"""
import os
import sys

sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl
import gatedcue as G

CKPT = os.environ.get("CKPT", "cache/emu_perform_track.pkl")
PLAYW = 0x0B6C4264
PERFORMW = 0x0BA371D4
ZSTATE = 0x0C0E75BC
KEY86 = 0x0B6C7A28 + 86
A_XY = (165, 264)
OUT_XY = (50, 264)


def load():
    m = Machine.load(CKPT)
    G.install(m.mem)
    return m


def run_steps(m, n, frame=None):
    m.mem.dev.panel_frame = frame if frame is not None else panl.build_frame()
    return m.cpu.run(limit=n)


def mode(m):
    return m.mem.r32(PLAYW)


def state(m):
    return (m.mem.r8(G.GLATCH), m.mem.r8(G.GPRIOR),
            m.mem.r8(G.GPEND), mode(m), m.mem.r32(ZSTATE))


def play(m):
    """Stock panel PlayPause press (proven 3<->5)."""
    run_steps(m, 3_000_000, panl.build_frame(keys=["PlayPause"]))
    run_steps(m, 10_000_000)


def press_hold(m, xy, hold=12_000_000):
    run_steps(m, hold, panl.build_frame(touch=xy))


def release(m, after=30_000_000):
    run_steps(m, after)


def check(tag, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {tag} {extra}")
    return bool(cond)


def t_off():
    print("[OFF] playing -> A -> release stays playing (stock latch)")
    m = load()
    mem = m.mem
    ok = True
    ok &= check("perform screen", mem.r32(PERFORMW) == 7,
                hex(mem.r32(PERFORMW)))
    ok &= check("starts paused", mode(m) == 3, hex(mode(m)))
    play(m)
    ok &= check("playing after PlayPause", mode(m) == 5, hex(mode(m)))
    press_hold(m, A_XY)
    latch, prior, pend, md, zs = state(m)
    print(f"  held: latch={latch} prior={prior} pend={pend} "
          f"mode={md:#x} zone={zs:#x} key86={mem.r8(KEY86):#x}")
    ok &= check("OFF never arms", latch == 0, f"latch={latch}")
    ok &= check("stock A fired (zone/key)", zs == 0x17 or mem.r8(KEY86) & 0x40,
                f"zone={zs:#x} key86={mem.r8(KEY86):#x}")
    release(m)
    latch, prior, pend, md, zs = state(m)
    print(f"  released: latch={latch} pend={pend} mode={md:#x} zone={zs:#x}")
    ok &= check("still playing (stock)", md == 5, hex(md))
    ok &= check("latch clear, no pending", latch == 0 and pend == 0,
                f"latch={latch} pend={pend}")
    return ok


def t_on_playing():
    print("[ON] playing -> A -> release keeps playing (prior play resumes)")
    m = load()
    mem = m.mem
    ok = True
    mem.w8(G.GSEL, 1)
    play(m)
    ok &= check("playing", mode(m) == 5, hex(mode(m)))
    press_hold(m, A_XY)
    latch, prior, pend, md, zs = state(m)
    print(f"  held: latch={latch} prior={prior} pend={pend} "
          f"mode={md:#x} zone={zs:#x} key86={mem.r8(KEY86):#x}")
    ok &= check("armed", latch == 1, f"latch={latch}")
    ok &= check("prior==playing", prior == 1, f"prior={prior}")
    ok &= check("stock A fired", zs == 0x17 or mem.r8(KEY86) & 0x40,
                f"zone={zs:#x}")
    release(m)
    latch, prior, pend, md, zs = state(m)
    print(f"  released: latch={latch} pend={pend} mode={md:#x} zone={zs:#x}")
    ok &= check("keeps playing (no gate pause)", md == 5, hex(md))
    ok &= check("latch clear, no pending", latch == 0 and pend == 0,
                f"latch={latch} pend={pend}")
    return ok


def t_on_paused_gate():
    print("[ON] paused -> A (arm) -> stock play -> release pauses (gate)")
    m = load()
    mem = m.mem
    ok = True
    mem.w8(G.GSEL, 1)
    ok &= check("starts paused", mode(m) == 3, hex(mode(m)))
    press_hold(m, A_XY, hold=6_000_000)
    latch, prior, pend, md, zs = state(m)
    print(f"  armed: latch={latch} prior={prior} mode={md:#x} zone={zs:#x}")
    ok &= check("armed from pause", latch == 1 and prior == 0,
                f"latch={latch} prior={prior}")
    # Stock-driven 3 -> 5 while the pad stays armed (deck: the hot cue
    # itself would do this; emu track has no cue point so PlayPause, the
    # same consumer, stands in -- see module docstring).
    run_steps(m, 3_000_000, panl.build_frame(keys=["PlayPause"], touch=A_XY))
    run_steps(m, 6_000_000, panl.build_frame(touch=A_XY))
    latch, prior, pend, md, zs = state(m)
    print(f"  playing while held: latch={latch} mode={md:#x} zone={zs:#x}")
    ok &= check("now playing, still armed", md == 5 and latch == 1,
                f"mode={md:#x} latch={latch}")
    release(m)
    latch, prior, pend, md, zs = state(m)
    print(f"  released: latch={latch} pend={pend} mode={md:#x} zone={zs:#x} "
          f"count={mem.r8(G.GCOUNT)}")
    ok &= check("gated pause (5->3)", md == 3, hex(md))
    ok &= check("latch clear, pending settled", latch == 0 and pend == 0,
                f"latch={latch} pend={pend}")
    return ok


def t_on_paused_noop():
    print("[ON] paused -> A -> release, no play (empty cue safe no-op)")
    m = load()
    mem = m.mem
    ok = True
    mem.w8(G.GSEL, 1)
    press_hold(m, A_XY)
    latch, prior, pend, md, zs = state(m)
    print(f"  held: latch={latch} prior={prior} mode={md:#x}")
    ok &= check("armed", latch == 1 and prior == 0,
                f"latch={latch} prior={prior}")
    release(m)
    latch, prior, pend, md, zs = state(m)
    print(f"  released: latch={latch} pend={pend} mode={md:#x}")
    ok &= check("stays paused", md == 3, hex(md))
    ok &= check("no stuck pending", latch == 0 and pend == 0,
                f"latch={latch} pend={pend}")
    return ok


def t_outside():
    print("[ON] press outside A/B/C never arms")
    m = load()
    mem = m.mem
    ok = True
    mem.w8(G.GSEL, 1)
    press_hold(m, OUT_XY)
    latch, prior, pend, md, zs = state(m)
    print(f"  held outside: latch={latch} mode={md:#x} zone={zs:#x}")
    ok &= check("not armed", latch == 0, f"latch={latch}")
    release(m)
    latch, prior, pend, md, zs = state(m)
    ok &= check("settled, still paused", md == 3 and latch == 0 and pend == 0,
                f"mode={md:#x} latch={latch} pend={pend}")
    return ok


def run():
    ok = True
    try:
        ok &= t_off()
    except Exception as e:
        print("  [FAIL] OFF raised", repr(e))
        ok = False
    try:
        ok &= t_on_playing()
    except Exception as e:
        print("  [FAIL] ON-playing raised", repr(e))
        ok = False
    try:
        ok &= t_on_paused_gate()
    except Exception as e:
        print("  [FAIL] ON-paused-gate raised", repr(e))
        ok = False
    try:
        ok &= t_on_paused_noop()
    except Exception as e:
        print("  [FAIL] ON-paused-noop raised", repr(e))
        ok = False
    try:
        ok &= t_outside()
    except Exception as e:
        print("  [FAIL] outside raised", repr(e))
        ok = False
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
