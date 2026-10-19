#!/usr/bin/env python3
"""keyshift_reload_emu.py -- EMULATOR ONLY: the deck-safe delivery of
real-time key shift. Installs the SH one-shot reload cave
(build/keyshift/ksr.bin, tools/keyshift_dsp/build_ksr.sh) exactly as the
deck installer would (cave bytes, ksr_prev = live flush pool, pool -> cave,
flag 'KSR1'), lets the firmware run it, and checks:
  * status 1 (boot routine returned 0), pools put back, hook unhooked,
  * the DSP now runs the patched program (call word + ks code in DDR),
  * the DSP keeps running (no error) afterwards.
Saves OUT (default cache/emu_ks_reloaded.pkl). Then:
  MOUNTED= STICK=cache/stick7.img PUSHES=7 NOPLAY=1 PLAY_STEPS=1.5e9 \
    SAVE_PLAYING=cache/emu_ks_playing.pkl \
    .venv/bin/python tools/dsp_play_test.py cache/emu_ks_reloaded.pkl
  SEMIS=2 .venv/bin/python tools/keyshift_dsp_emu.py cache/emu_ks_playing.pkl
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402

B = os.path.join("build", "keyshift")
POOL_FLUSH = 0x08C92E20


def syms():
    s = {}
    for l in open(os.path.join(B, "ksr.syms")):
        a, _, n = l.split()
        s[n.lstrip("_")] = int(a, 16)
    return s


def main():
    snap = sys.argv[1] if len(sys.argv) > 1 else "cache/emu_dsp_mounted6.pkl"
    out = os.environ.get("OUT", "cache/emu_ks_reloaded.pkl")
    t0 = time.time()
    S = syms()
    blob = open(os.path.join(B, "ksr.bin"), "rb").read()
    ks = open(os.path.join(B, "ks.bin"), "rb").read()
    m = Machine.load(snap)
    mem, d = m.mem, m.mem.dev.dspemu
    m.cpu.run(limit=10_000_000)
    base = S["ksr_entry"]
    for i, b in enumerate(blob):
        mem.w8(base + i, b)
    prev = mem.r32(POOL_FLUSH)
    mem.w32(S["ksr_prev"], prev)
    mem.w32(POOL_FLUSH, base)
    mem.w32(S["ksr_flag"], 0x4B535231)
    print(f"installed at {base:#x}, prev flush {prev:#x}", flush=True)
    st = 0
    for i in range(200):
        m.cpu.run(limit=10_000_000)
        st = mem.r32(S["ksr_status"])
        if st:
            break
    print(f"status {st:#x} after ~{(i+1)*10}M steps; flag {mem.r32(S['ksr_flag']):#x} "
          f"pool {mem.r32(POOL_FLUSH):#x} len {mem.r32(0x08D78AC8):#x} "
          f"src {mem.r32(0x08C37918):#x} [{time.time()-t0:.0f}s]", flush=True)
    call = d.bus.read(0xC0008D64, 4)
    ddr = bytes(d.bus.read(0xC005F4C0 + i, 1) for i in range(len(ks)))
    img_ok = mem.r32(0x080174D4) == 0x0027D290
    print(f"DSP call word {call:#x}, ks code in DDR {'==' if ddr == ks else '!='} ks.bin, "
          f"SH program copy untouched {img_ok}", flush=True)
    for k in range(3):
        m.cpu.run(limit=100_000_000)
        print(f"  +{(k+1)*100}M: DSP err={d.error} running={d.running} pc={d.cpu.pc:#x}", flush=True)
    ok = (st == 1 and call == 0x00ACEC10 and ddr == ks and img_ok
          and mem.r32(POOL_FLUSH) == prev and mem.r32(0x08D78AC8) == 0x5F4C0
          and mem.r32(0x08C37918) == 0xA800E770 and d.error is None and d.running)
    m.save(out)
    print("saved", out, "RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
