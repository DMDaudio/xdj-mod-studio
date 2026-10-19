#!/usr/bin/env python3
"""flac_diff_trace.py prep|trace -- where does the stock firmware decide a
file's type on LOAD? Differential trace: control.wav vs test.flac from the
same folder (stick /tmp/flacstick, made by tools/flac_emu_proof.py).

  prep   C core + HLE DSP (loads complete): remount the stick, Browse ->
         [FOLDER] -> Contents -> FlacTest; saves cache/flacdiff_list.pkl
         (cursor on row 0 = control.wav)
  trace  py core from that snapshot, twice: push on row 0 (WAV) / row 1
         (FLAC); records every executed pc for N steps; prints the pcs
         ONLY one load ran (by function cluster) + both screens.
"""
import os
import sys
os.environ["XDJ_HLE_DSP"] = "1"
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

ROOT = "/tmp/flacstick"
CK = "cache/flacdiff_list.pkl"
ROT = 0x0B6C7B48


def key(m, k, n=15_000_000):
    m.mem.dev.panel_frame = panl.build_frame(keys=[k])
    m.cpu.run(limit=3_000_000)
    m.mem.dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=n)


def prep():
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    m = Machine.load("cache/emu_realusb.pkl")
    dev, mem = m.mem.dev, m.mem
    u = dev._usb()
    u.unplug()
    m.cpu.run(limit=20_000_000)
    disk = open_disk(ROOT)
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(6):
        m.cpu.run(limit=250_000_000)
    key(m, "Browse")
    mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)
    m.cpu.run(limit=15_000_000)
    for i in range(3):
        if i == 2:                      # one push before the folder opens
            m.save("cache/flacdiff_parent.pkl")
        key(m, "RotaryPush", 30_000_000)
    fb_png(m, "screenshots/flacdiff_list.png")
    m.save(CK)
    print("saved", CK)


def trace(steps):
    sets = {}
    for name, rot in (("wav", 0), ("flac", 1)):
        m = Machine.load(CK, core="py")
        mem, c = m.mem, m.cpu
        if rot:
            mem.w16(ROT, (mem.r16(ROT) + rot) & 0xFFFF)
            c.run(limit=20_000_000)
        seen = set()
        c.trace = lambda pc, op: seen.add(pc)
        key(m, "RotaryPush", steps)
        c.trace = None
        fb_png(m, f"screenshots/flacdiff_{name}.png")
        sets[name] = seen
        print(f"{name}: {len(seen)} distinct pcs, mode {mem.r32(0x0B6C4264)}")
    for a, b in (("wav", "flac"), ("flac", "wav")):
        only = sorted(p for p in sets[a] - sets[b] if 0x08000000 <= p < 0x0A000000)
        clusters, start, prev = [], None, None
        for p in only:
            if prev is None or p - prev > 0x40:
                if start is not None:
                    clusters.append((start, prev))
                start = p
            prev = p
        if start is not None:
            clusters.append((start, prev))
        print(f"--- only {a}: {len(only)} pcs in {len(clusters)} clusters")
        for s, e in clusters[:60]:
            print(f"   {s:#010x}-{e:#010x}")


def opentrace(steps):
    """trace the push that OPENS the FlacTest folder (files parsed here)"""
    import collections
    W = {0x08C5635E: "wavread", 0x08DE0E7A: "sizefn", 0x08C6585A: "pool2",
         0x08C61A6C: "fread", 0x08C6212C: "fsread", 0x08C62E1C: "fseek3",
         0x08C62EF0: "fseek", 0x08C01E78: "typedisp"}
    m = Machine.load("cache/flacdiff_parent.pkl", core="py")
    c = m.cpu
    log = []
    c.trace = lambda pc, op: log.append((W[pc], hex(c.pr), [hex(c.gr(i)) for i in range(4, 8)])) \
        if pc in W else None
    key(m, "RotaryPush", steps)
    c.trace = None
    print(len(log), "calls", collections.Counter((a, b) for a, b, _ in log).most_common(15))
    for e in log[:60]:
        print("  ", e)


if __name__ == "__main__":
    if sys.argv[1] == "open":
        opentrace(int(float(sys.argv[2])) if len(sys.argv) > 2 else 40_000_000)
    elif sys.argv[1] == "prep":
        prep()
    else:
        trace(int(float(sys.argv[2])) if len(sys.argv) > 2 else 60_000_000)
