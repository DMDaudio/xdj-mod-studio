#!/usr/bin/env python3
"""dsp_side_watch.py SNAP OUT [MASKED|UNMASKED] -- EMULATOR ONLY.

Watch the DSP side of the flash handshake: which DSP pc writes the
0x1183FFF0/0x1183FFF4 handshake words (the flags the boot's stage-1/5
polls wait for), in a cave-free parking-loop rehearsal. C-core store
watch (cfast.watch, approximate commit pc) + a decode dump around the
writer so its guard conditions can be read offline.

Purpose: the deck's wedge is now known to be on the DSP side (stage-5
poll never sees FFF4=1 while the DSP program is alive); this names the
writer and its gating logic so the cave repair can target exactly what
the deck's warm state holds stuck.
"""
import sys
import time
import collections
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from emu.c6x import cfast                 # noqa: E402

BOOT = 0x08C3777C
PARK = 0x081DFFF0
WATCH_LO, WATCH_HI = 0x1183FFE0, 0x11840000


def stage(pc):
    if not (0x08C374F2 <= pc < 0x08C378C0):
        return "other"
    for lo, hi, name in ((0x08C374F2, 0x08C37554, "st0"),
                         (0x08C37554, 0x08C37634, "st1"),
                         (0x08C37634, 0x08C376AE, "st2"),
                         (0x08C376AE, 0x08C3771A, "st3/4"),
                         (0x08C3771A, 0x08C3777C, "st5"),
                         (0x08C3777C, 0x08C378C0, "BOOT")):
        if lo <= pc < hi:
            return name
    return "other"


def main():
    snap, out = sys.argv[1], sys.argv[2]
    regime = sys.argv[3].upper() if len(sys.argv) > 3 else "MASKED"
    t0 = time.time()
    m = Machine.load(snap)
    mem, c, d = m.mem, m.cpu, m.mem.dev.dspemu
    assert d is not None and getattr(d.cpu, "lib", None), "needs the C DSP core"
    hits = []
    owrite = d.bus.write

    def busw(a, n, v, ow=owrite):
        if a < WATCH_HI and a + n > WATCH_LO:
            hits.append((a, v, d.cpu.pc, d.cycles))
        return ow(a, n, v)
    d.bus.write = busw
    if getattr(d.cpu, "lib", None):
        cfast.watch(d.cpu, WATCH_LO, WATCH_HI)
    m.cpu.run(limit=20_000_000)
    print(f"before: DSP cycles {d.cycles/1e6:.0f}M pc={d.cpu.pc:#x} "
          f"mode={mem.r32(0x0B6C4264):#x}", flush=True)
    saved = c.__getstate__()
    old_park = [mem.r16(PARK), mem.r16(PARK + 2)]
    mem.w16(PARK, 0xAFFE)
    mem.w16(PARK + 2, 0x0009)
    idle = getattr(m, "idle_skip", True)
    m.idle_skip = False
    d.sh.write(0x0405012E, 1, d.sh.read(0x0405012E, 1) & 0xFE)
    c.pc = PARK
    m.cpu.run(limit=2_000_000)
    print(f"reset held: in_reset={d.in_reset}", flush=True)
    if regime == "MASKED":
        c.sr |= 0x10000000
    c.pr = PARK
    c.pc = BOOT
    ret = None
    for i in range(400):
        c.run(limit=10_000_000)
        f4 = d.bus.read(0x1183FFF4, 4)
        print(f"+{(i+1)*10}M sh={stage(c.pc):7s} dsp={d.cpu.pc:#x} "
              f"hpic={d.hpic:#x} pin={d.busy_pin()} FFF4={f4:#x}", flush=True)
        if c.pc in (PARK, PARK + 2):
            ret = c.gr(0)
            break
    print(f"boot returned {ret} [{time.time()-t0:.0f}s]", flush=True)
    n, log = cfast.watch_log(d.cpu)
    print(f"c-core watch log entries: {n}; bus-level hits: {len(hits)}",
          flush=True)
    if hits:
        print("bus-level writes to the flag region:", flush=True)
        for a, v, pc, cy in hits[:20]:
            print(f"  bus  addr={a:#x} val={v:#x} dsp_pc={pc:#x} "
                  f"cyc={cy/1e6:.1f}M", flush=True)
    if log:
        sig = collections.Counter((hex(p), hex(a)) for p, a, v, cy in log)
        print("pcs by addr:", sig.most_common(10), flush=True)
        for p, a, v, cy in log:
            if a in (0x1183FFF0, 0x1183FFF4):
                print(f"  FLAG  pc={p:#x} addr={a:#x} val={v:#x} cyc={cy/1e6:.1f}M",
                      flush=True)
    # decode the neighborhood of the first FFF4 writer
    writers = sorted({p for p, a, v, cy in log if a == 0x1183FFF4})
    for wpc in writers[:3]:
        base = wpc & ~0x7F
        blob = d.bus.dump(base, 0x100)
        try:
            code = __import__("emu.c6x.decode", fromlist=["decode_bytes"]
                              ).decode_bytes(blob, base)
            print(f"--- DSP code around writer {wpc:#x} ---", flush=True)
            for a in sorted(code):
                if a in (base,):
                    continue
                ins = code.get(a)
                if ins is None:
                    continue
                mark = " >>>" if (wpc - a) // 4 < 6 and a <= wpc < a + 4 * 6 else "    "
                print(f"{mark} {a:#09x} {ins.mnem:8s} u={getattr(ins,'unit','?')} "
                      f"r={getattr(ins,'regs','')}", flush=True)
                if a > wpc + 0x40:
                    break
        except Exception as e:      # noqa: BLE001
            print(f"decode failed at {wpc:#x}: {e}", flush=True)
    saved["cycles"], saved["steps"] = c.cycles, c.steps
    c.__setstate__(saved)
    mem.w16(PARK, old_park[0])
    mem.w16(PARK + 2, old_park[1])
    m.idle_skip = idle
    m.save(out)
    print("saved", out, "RESULT:", "BOOT OK" if ret == 0 else "BOOT FAILED",
          flush=True)


if __name__ == "__main__":
    main()
