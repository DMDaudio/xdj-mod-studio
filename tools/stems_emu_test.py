#!/usr/bin/env python3
"""stems_emu_test.py [MASK] -- live stem mute on the emulated deck.

Builds a STEMS-4 WAV (tools/stems4.py) from four distinct test tones,
mounts it, browses to it, installs the stem mixer (tools/stems/sm.c,
hooked on the media server's audio read) with PERFORM STEMS mask MASK
(bit 0 vocals, 1 drums, 2 bass, 3 other = muted; default 0b0101 =
vocals + bass muted), loads the track, and checks every audio read the
deck makes inside the sample data returns exactly the clipped sum of the
UN-muted stems. MASK 0 checks the pass-through (= the plain mix).
"""
import os
import struct
import subprocess
import sys
import wave

sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402

ROOT_STICK = os.path.join(os.environ.get("TMPDIR", "/tmp"), "smstick")
FREAD_RET = 0x08D5FD42                   # return address after the hooked jsr
HOOK = 0x08D5FFA8


def make_file():
    import stems4 as S4
    d = os.path.join(ROOT_STICK, "Contents", "StemTest")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "S stems.wav")
    if os.path.exists(out):
        return out
    td = os.path.join(ROOT_STICK, "..", "smsrc")
    os.makedirs(td, exist_ok=True)
    sr, secs = 44100, 40
    t = np.arange(sr * secs) / sr
    paths = {}
    for i, k in enumerate(S4.ORDER):
        x = (0.18 * np.sin(2 * np.pi * (110 * (i + 2)) * t) * 32767).astype(np.int16)
        p = os.path.join(td, k + ".wav")
        with wave.open(p, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(np.repeat(x, 2).tobytes())
        paths[k] = p
    mix = os.path.join(td, "mix.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y"]
                   + sum((["-i", paths[k]] for k in S4.ORDER), [])
                   + ["-filter_complex", "amix=inputs=4:normalize=0",
                      "-metadata", "artist=Stem Test", "-metadata", "title=Four Tones",
                      "-c:a", "pcm_s16le", mix], check=True)
    S4.build(mix, out, paths)
    return out


def build_mixer():
    import perform_pads as PP
    env = dict(os.environ, PMASK=hex(PP.PMASK))
    subprocess.run(["sh", "tools/stems/build.sh"], check=True, env=env,
                   capture_output=True)
    syms = {}
    for ln in open("build/stems/sm.syms"):
        a, _, n = ln.split()
        syms[n.lstrip("_")] = int(a, 16)
    return open("build/stems/sm.bin", "rb").read(), syms, PP.PMASK


def main():
    mask = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0b0101
    import stems4 as S4
    from emu.machine import Machine
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    from emu import panl
    out = make_file()
    d_off, d_len, big, st, mix = S4.read(out)
    want = S4.expected(st, mask).tobytes() if mask else mix.tobytes()
    code, syms, pmask = build_mixer()

    m = Machine.load("cache/emu_realusb.pkl")
    dev, mem = m.mem.dev, m.mem
    u = dev._usb()
    u.unplug()
    m.cpu.run(limit=20_000_000)
    disk = open_disk(ROOT_STICK)
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
    for _ in range(3):
        key("RotaryPush", 30_000_000)       # [FOLDER] -> Contents -> StemTest

    base = syms["sm_table"]
    for a in range(base, syms["end"], 4):
        mem.w32(a, 0)
    for i, b in enumerate(code):
        mem.w8(base + i, b)
    mem.w32(syms["sm_prev"], mem.r32(HOOK))   # stock fread
    mem.w8(pmask, mask)
    mem.w32(HOOK, syms["sm_fread"])
    m.save("/tmp/smstick_ready.pkl")
    m = Machine.load("/tmp/smstick_ready.pkl", core="py")
    mem, dev, c = m.mem, m.mem.dev, m.cpu
    pend, res = {}, []

    fps = []

    def tr(pc, op):
        if pc == syms["sm_fread"]:
            fp = c.gr(7) & 0x1FFFFFFF
            fps.append(c.gr(7))
            pend["pos"] = mem.r32(fp + 0x2C)
            pend["buf"] = c.gr(4) & 0x1FFFFFFF
        if pc == FREAD_RET and "pos" in pend:
            pos, buf = pend.pop("pos"), pend.pop("buf")
            n = c.gr(0) * 1
            got = bytes(mem.r8(buf + i) for i in range(min(256, max(0, n))))
            if d_off <= pos < d_off + d_len and len(got) >= 64:
                exp = want[pos - d_off:pos - d_off + len(got)]
                plain = mix.tobytes()[pos - d_off:pos - d_off + len(got)]
                res.append((pos, got == exp, got == plain))
    c.trace = tr
    dev.panel_frame = panl.build_frame(keys=["RotaryPush"])
    c.run(limit=3_000_000)
    dev.panel_frame = panl.build_frame()
    c.run(limit=int(os.environ.get("STEPS", "80000000")))
    c.trace = None
    hits = sum(1 for _, e, _p in res if e)
    print(f"mask {mask:#06b}: {len(res)} audio reads inside the sample data, "
          f"{hits} returned the expected stem mix, "
          f"{sum(1 for _, e, p in res if p and not e)} returned the plain mix; "
          f"mixer calls handled: {mem.r32(syms['sm_hits'])}")
    ok = len(res) > 0 and hits == len(res) and (mask == 0 or mem.r32(syms["sm_hits"]) > 0)
    # The real deck reads in much bigger pieces than the emulated one
    # (2026-10-12: zero hits on hardware with the old 16 KB limit). Call the
    # mixer directly with deck-sized / odd / straddling reads.
    # NOTE: direct calls trap through Machine.run's per-step tracer
    # (~830 us/step), so a single call takes the better part of an hour --
    # and the media server barely turns without an audio clock anyway. The
    # math on deck-sized reads is proven natively (stems_native_test.py);
    # run this section only with DIRECT=1 (overnight).
    if mask and fps and os.environ.get("DIRECT"):
        fp, HALT, BUF = fps[-1], 0x0F300000, 0x0F200000

        def call(fn, *args):
            for i, a in enumerate(args):
                c.set_gr(4 + i, a & 0xFFFFFFFF)
            c.pr = HALT
            c.pc = fn
            r = m.run(limit=400_000_000, stop_on_pc=[HALT])
            assert "halt" in str(r), r
            return c.gr(0)
        bigok = True
        for pos, n in ((d_off + 4096, 65536), (d_off + 128, 131072),
                       (d_off - 100, 50000), (d_off + 777 * 4, 40002),
                       (d_off + d_len - 30000, 65536)):
            call(0x08C62E1C, fp, pos, 0)
            got_items = call(syms["sm_fread"], BUF, 1, n, fp)
            got = bytes(mem.r8(BUF + i) for i in range(got_items))
            raw = open(make_file(), "rb").read()[pos:pos + got_items]
            exp = bytearray(raw)
            lo, hi = d_off, d_off + d_len
            a = max(pos, lo)
            a = lo + ((a - lo + 3) & ~3)
            b = min(pos + got_items, hi)
            b = lo + ((b - lo) & ~3)
            if a < b:
                exp[a - pos:b - pos] = want[a - lo:b - lo]
            good = got == bytes(exp)
            posnow = mem.r32((fp & 0x1FFFFFFF) + 0x2C)
            good &= posnow == pos + got_items
            print(f"   direct read pos {pos - d_off:+d} n {n}: {got_items} B, "
                  f"{'mix exact' if good else 'WRONG'}, file pos {'ok' if posnow == pos + got_items else 'WRONG'}")
            bigok &= good
        ok &= bigok
        print(f"   counters: calls {mem.r32(syms['sm_calls'])} max read {mem.r32(syms['sm_maxn'])} "
              f"files {mem.r32(syms['sm_files'])}")
    else:
        print("   direct-call section skipped (DIRECT not set; see note above)")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
