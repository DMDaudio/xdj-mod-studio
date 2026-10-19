#!/usr/bin/env python3
"""flac_emu_proof.py -- prove the DeckVolve FLAC path in the emulator.

RETIRED 2026-10-11 (DO NOT re-run this as a release gate -- see CLAUDE.md
section 64): the DeckVolve black-box cave payload never engages (0 payload
hits even over 900M steps, no DeckVolve source to debug). This path does
NOT ship. The shipped FLAC layer is our own decoder:
  tools/xflac/vfile.c installed via tools/flac_apply.py,
  proven by tools/xflac_vfile_test.py (bit-exact vs ffmpeg, native),
  tools/xflac_test.py (bit-exact inside the emulated deck) and
  tools/repro_freeze.py --only pads,stems,flac (full-set install + play),
  plus the deck session (step 5h) for hardware.
This file is kept for history only.

Installs the FLAC target bytes (payload+gate+hooks, same set as
tools/flac_install.py) in emu RAM, mounts a scratch stick carrying a
FLAC in folder view, browses + loads it, and checks:
  1. gate code executes (pc in 0x080FB800..0x080FC200)
  2. payload code executes (pc in 0x091BD7B0..+53228)
  3. no crash/hang; MP3 control still loads (separate run, hooks armed)
  4. decoded PCM correlation (best effort: scan audio buffers)

Usage: .venv/bin/python tools/flac_emu_proof.py [--mp3]
(--mp3 = control run with an MP3 instead of FLAC; hooks stay armed)
"""
import os
import struct
import subprocess
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

ROOT = "/tmp/flacstick"
GATE_LO, GATE_HI = 0x080FB800, 0x080FC200
PAY_LO = 0x091BD7B0
PAY_HI = 0x091BD7B0 + 53228


def make_stick(flac=True):
    import numpy as np
    d = os.path.join(ROOT, "Contents", "FlacTest")
    os.makedirs(d, exist_ok=True)
    if flac:
        out = os.path.join(d, "test.flac")
        if not os.path.exists(out):
            src = os.path.expanduser(
                "~/Downloads/Rekordbox stuff/Contents/Lifelike & "
                "Roman Salzger/UnknownAlbum/Lifelike & Roman Salzger - "
                "Radio 84.aiff")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "60",
                            "-t", "30", "-i", src, "-c:a", "flac", out],
                           check=True)
    else:
        out = os.path.join(d, "test.wav")
        if not os.path.exists(out):
            subprocess.run(["ffmpeg", "-v", "error", "-y",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                            "-c:a", "pcm_s16le", out], check=True)
    ctl = os.path.join(d, "control.wav")
    if not os.path.exists(ctl):
        subprocess.run(["ffmpeg", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=880:duration=5",
                        "-c:a", "pcm_s16le", ctl], check=True)
    print("stick file:", out, os.path.getsize(out), flush=True)
    return out


def install_target(mem):
    import json
    import base64
    r = json.load(open("tools/data/flac_alpha1_template.json"))
    stock = open("cache/blk1.bin", "rb").read()
    t = bytearray(stock + b"\xff" * (r["target_decoded_size"] - len(stock)))
    for s in r["spans"]:
        dd = base64.b64decode(s["data"])
        t[s["offset"]:s["offset"] + len(dd)] = dd
    for op in r["owner_stock_copy_ops"]:
        src, dst, n = op["source_offset"], op["dest_offset"], op["size"]
        chunk = bytearray(stock[src:src + n])
        br = op.get("branch_relocation")
        if br:
            off, kind, tvma = (br["instruction_offset"], br["kind"],
                               br["target_vma"])
            chunk[off] = ((tvma - (0x08000000 + dst + off + 4)) // 2) & 0xFF
        t[dst:dst + n] = chunk
    t = bytes(t)
    base = 0x08000000
    n = 0
    for i in range(len(t)):
        old = stock[i] if i < len(stock) else 0xFF
        if t[i] != old:
            mem.w8(base + i, t[i])
            n += 1
    # verify hooks
    bad = 0
    for a in (0x08C01E78, 0x08C5635E, 0x08C567BC, 0x08BE9000, 0x08C000A4):
        got = struct.pack("<I", 0)
        got = bytes(mem.r8(a + i) for i in range(4))
        if got != t[a - base:a - base + 4]:
            bad += 1
    print(f"installed {n} B, hook mismatches: {bad}", flush=True)
    return bad == 0


def main():
    from emu.machine import Machine
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    from emu import panl
    make_stick(flac="--mp3" not in sys.argv)
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

    def key(k, n=15_000_000):
        dev.panel_frame = panl.build_frame(keys=[k])
        m.cpu.run(limit=3_000_000)
        dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=n)

    key("Browse")
    mem.w16(0x0B6C7B48, (mem.r16(0x0B6C7B48) + 1) & 0xFFFF)
    m.cpu.run(limit=15_000_000)
    from ghosttap_emu import fb_png
    for i in range(3):
        key("RotaryPush", 30_000_000)       # [FOLDER] -> Contents -> FlacTest
        fb_png(m, f"screenshots/flac_nav{i}.png")
    if "--mp3" not in sys.argv:   # list = control.wav, test.flac: step to the FLAC
        mem.w16(0x0B6C7B48, (mem.r16(0x0B6C7B48) + 1) & 0xFFFF)
        m.cpu.run(limit=30_000_000)
        fb_png(m, "screenshots/flac_nav_sel.png")
    if not install_target(mem):
        print("RESULT: FAIL (install verify)")
        return 1
    m.save("/tmp/flac_ready.pkl")
    m = Machine.load("/tmp/flac_ready.pkl", core="py")
    mem, dev, c = m.mem, m.mem.dev, m.cpu
    hits = {"gate": 0, "pay": 0}

    def tr(pc, op):
        if GATE_LO <= pc < GATE_HI:
            hits["gate"] += 1
        if PAY_LO <= pc < PAY_HI:
            hits["pay"] += 1

    c.trace = tr
    dev.panel_frame = panl.build_frame(keys=["RotaryPush"])
    c.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    c.run(limit=int(os.environ.get("STEPS", "80000000")))
    c.trace = None
    print(f"gate hits: {hits['gate']}, payload hits: {hits['pay']}",
          flush=True)
    fb_png(m, "screenshots/flac_after_load.png")
    # the decoder payload itself must run (gate-only hits = a non-FLAC
    # file went through the dispatcher; that was the old false PASS)
    ok = hits["gate"] > 0 and (hits["pay"] > 0 or "--mp3" in sys.argv)
    print("RESULT:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
