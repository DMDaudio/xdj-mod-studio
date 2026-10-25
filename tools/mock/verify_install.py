#!/usr/bin/env python3
"""verify_install.py MACHINE.pkl -- after a mock install (mock_install.py),
check the emulated deck's RAM holds exactly what every installer meant to
put there: each mod's code/data bytes, and each hook pointing at our code.
"""
import os
import struct
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools"), os.path.join(ROOT, "mods")]
os.chdir(ROOT)
from emu.machine import Machine          # noqa: E402


def main():
    m = Machine.load(sys.argv[1])
    mem = m.mem
    ok = True

    def region(name, base, want, skip=()):
        nonlocal ok
        got = bytes(mem.r8(base + i) for i in range(len(want)))
        bad = [i for i in range(len(want)) if got[i] != want[i] and (base + i) not in skip]
        print(f"  {'ok  ' if not bad else 'FAIL'} {name:22s} {len(want):6d} B @ {base:#010x}"
              + (f"  first diff +{bad[0]:#x}" if bad else ""))
        ok &= not bad

    def hook(name, pool, want):
        nonlocal ok
        v = mem.r32(pool)
        good = v == want if not callable(want) else want(v)
        print(f"  {'ok  ' if good else 'FAIL'} hook {name:17s} [{pool:#010x}] = {v:#010x}")
        ok &= good

    import rgb_overview as RO
    import rgb_detail as RD
    import vredirect as VR
    import mysettings as MS
    import perform_pads as PP
    import theme_switch as TS

    for a, b in RO.plan(RO.MODES["rgb"])[1]:
        if a == RO.MODE_ADDR:                 # any installed style is fine
            if mem.r8(a) not in RO.MODES.values():
                print(f"  FAIL waveform mode byte {mem.r8(a):#x}")
                ok = False
            continue
        if mem.r8(a) != b and not (RO.SNAP <= a < RO.SNAP + 0x44000):
            print(f"  FAIL overview byte {a:#x}")
            ok = False
            break
    else:
        print("  ok   overview cave + snapshot routine")
    region("centre (DCAVE)", RD.DCAVE, RD.dcave_bytes())
    cwb, _cws = RD.cw_build()
    region("centre colour module", RD.CW_BASE, cwb)
    region("PERFORM strip adapter", RD.DCAVE2, RD.dcave2_bytes())
    for name_, a_, v_ in (("centre insn", RD.INSN, RD.INSN_NEW),
                          ("centre row patch", RD.ROWINC[0], RD.ROWINC[2]),
                          ("PERFORM strip insn", RD.INSN2, RD.INSN2_NEW),
                          ("PERFORM strip row patch", RD.ROWINC2[0], RD.ROWINC2[2])):
        g_ = mem.r16(a_) == v_
        print(f"  {'ok  ' if g_ else 'FAIL'} {name_} [{a_:#x}] = {mem.r16(a_):#06x}")
        ok &= g_
    for a, b in VR.plan():
        if mem.r8(a) != b and a != VR.HOOK and not (VR.HOOK <= a < VR.HOOK + 4):
            print(f"  FAIL versions byte {a:#x}")
            ok = False
            break
    else:
        print("  ok   versions code")
    region("MY SETTINGS rows", MS.MS, MS.image())
    for base, blob in PP.blobs():
        region("PERFORM pads", base, blob)
    for pool, old, new in MS.hooks():
        hook("MY SETTINGS", pool, new)
    import utility as UT                  # UTILITY rows (5u)
    _, us = UT.build()
    for p_ in UT.POOLS:
        hook("UTILITY table", p_, us["ut_tab"])
    hook("UTILITY count", UT.COUNT, UT.STOCK_N + UT.NOURS)
    ub = UT.image(bytes(mem.r8(us["ut_tab"] + i) for i in range(UT.STOCK_N * UT.REC)))[1]
    region("UTILITY module", us["ut_head"], ub,
           skip=range(us["ut_tab"], us["ut_tab"] + (UT.STOCK_N + UT.NOURS) * UT.REC))
    for pool, old, new in PP.hooks():
        if pool == 0x08C92E20:            # flush hook: hot cues -> phase? -> pads
            continue
        hook("PERFORM pads", pool, new)
    # theme switching: code (entry word = on-switch) + tables
    (cb, code), (db, data) = TS.plan(TS.default_names())
    region("theme switch code", cb, code)
    region("theme tables", db, data, skip=range(db + 32, db + 52))
    # hot cue pages: hooked into the flush chain
    syms = {}
    for ln in open("build/hotcue8/hc.syms"):
        a, _, n = ln.split()
        syms[n.lstrip("_")] = int(a, 16)
    flush = mem.r32(0x08C92E20)
    # optional phase meter (deck_session --phasemeter) sits in front of the
    # hot cues: follow its pm_prev
    pm = {}
    for ln in open("build/phasemeter/pm.syms"):
        a, _, n = ln.split()
        pm[n.lstrip("_")] = int(a, 16)
    if flush == pm["pm_entry"]:
        print(f"  ok   phase meter at the head of the flush chain")
        flush = mem.r32(pm["pm_prev"])
        print(f"  {'ok  ' if flush == syms['hc_entry'] else 'FAIL'} chain: phase meter -> {flush:#x} (hot cues)")
        ok &= flush == syms["hc_entry"]
    else:
        hook("2DG flush chain", 0x08C92E20, lambda v: v == syms["hc_entry"])
    prev = mem.r32(syms["hc_prev"])
    pads_flush = [n for p, o, n in PP.hooks() if p == 0x08C92E20][0]
    print(f"  {'ok  ' if prev == pads_flush else 'FAIL'} chain: hot cues -> {prev:#x} (pads)")
    ok &= prev == pads_flush
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
