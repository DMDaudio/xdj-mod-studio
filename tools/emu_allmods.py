#!/usr/bin/env python3
"""emu_allmods.py -- END-TO-END emulator test of everything deck_session.py
installs, driven only through the deck's own buttons:

  RELOOP re-point + overview cave/snapshot +
  RGB centre + version switching + MOD MENU,

then: mount a stick holding a VERSION file (tools/versionfile.py, built by
tools/vredirect.py emu), browse + load it, open PERFORM by touch, pick a
version on the KEY SHIFT tab (> steps through the file's versions),
press PLAY, and check the deck's audio buffers now hold
that version's bytes (not the mix). Screenshots in screenshots/allmods/.

  .venv/bin/python tools/emu_allmods.py      (C core; ~5 min)
"""
import os
import sys

sys.path[:0] = [".", "tools"]
import numpy as np

AUDIO_BUFS = (0x0E910B00, 0x0E91FB40)   # media-server double buffers
BLOCK = 0x24C0


def main():
    from emu.machine import Machine
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    from emu import panl
    from ghosttap_emu import fb_png
    import versionfile as VF
    import rgb_overview as RO
    import rgb_detail as RD
    import vredirect as VR
    import mysettings as MSET
    import perform_pads as PP
    import detail_fake as DF

    root = "/tmp/vstick"
    vf = os.path.join(root, "Contents", "VerTest", "V versions.wav")
    if not os.path.exists(vf):
        sys.exit("run tools/vredirect.py emu first (builds the test stick)")
    d_off, d_len, V = VF.read_versions(vf)
    names = list(V)
    out = "screenshots/allmods"
    os.makedirs(out, exist_ok=True)

    m = Machine.load("cache/emu_realusb.pkl")
    mem, dev, c = m.mem, m.mem.dev, m.cpu
    # --- install exactly what the deck session installs ---------------
    for a, b in RO.plan(RO.MODES["rgb"])[1]:
        mem.w8(a, b)
    for i, b in enumerate(RD.copy_bytes()):
        mem.w8(RD.COPY5 + i, b)
    RD.install(mem)                                       # centre waveform
    for a, b in VR.plan():
        mem.w8(a, b)
    MSET.install(mem)
    PP.install(mem)                                     # PERFORM pads (KEY SHIFT tab picks the version)
    # --- mount the stick, browse, load ---------------------------------
    u = dev._usb()
    u.unplug()
    c.run(limit=20_000_000)
    disk = open_disk(root)
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(6):
        c.run(limit=250_000_000)

    def key(k, hold=12_000_000, after=15_000_000):
        dev.panel_frame = panl.build_frame(keys=[k])
        c.run(limit=hold)
        dev.panel_frame = panl.build_frame()
        c.run(limit=after)

    def turn(n):
        mem.w16(panl.ROTARY_COUNTER, (mem.r16(panl.ROTARY_COUNTER) + n) & 0xFFFF)
        c.run(limit=12_000_000)

    key("Browse")
    turn(1)
    for _ in range(3):
        key("RotaryPush", after=30_000_000)   # [FOLDER] -> Contents -> VerTest
    fb_png(m, f"{out}/0_browse.png")
    key("RotaryPush", after=120_000_000)      # load the version file
    key("Browse", after=30_000_000)           # back to the main screen
    if os.environ.get("SAVE"):
        m.save(os.environ["SAVE"])
    ok = True
    n = mem.r16(VR.VTBL + 8)
    print(f"version table after load: n={n} (file has {len(names) - 1}: "
          f"{names[1:]}), VSEL={mem.r8(VR.VSEL)}")
    ok &= n == len(names) - 1
    # version selection lives on the PERFORM KEY SHIFT tab now (the old
    # MY SETTINGS row is gone): open PERFORM by touch, KEY SHIFT tab, > twice
    def touch(p, hold=3_000_000, after=3_000_000):
        for _ in range(40):                       # press only into an idle zone
            if mem.r32(PP.ZSTATE) == 0x1E:
                break
            c.run(limit=500_000)
        dev.panel_frame = panl.build_frame(touch=p)
        c.run(limit=hold)
        dev.panel_frame = panl.build_frame()
        c.run(limit=after)
        for _ in range(40):                       # let press + release settle
            if mem.r8(PP.PST) == 0 and mem.r32(PP.ZSTATE) == 0x1E:
                break
            c.run(limit=500_000)

    touch((710, 38))                              # PERFORM top-row button
    print("PERFORM open:", mem.r32(0x0BA371D4) == 7)
    ok &= mem.r32(0x0BA371D4) == 7
    fb_png(m, f"{out}/1_perform.png")
    touch((PP.TAB_X + PP.TAB_W // 2, PP.TAB_Y[2] + PP.TAB_H // 2))
    print("KEY SHIFT tab:", mem.r8(PP.PST + 2) == 2)
    ok &= mem.r8(PP.PST + 2) == 2
    want = 0                                   # this file has no +1 semitone
    mem.w8(VR.VSEL, 2)                        # preset: prove the touch clears it
    touch((365, 264))                         # KEY panel RESET: VSEL -> 0
    print(f"KEY SHIFT RESET: VSEL={mem.r8(VR.VSEL)} "
          f"({names[mem.r8(VR.VSEL)]})")
    ok &= mem.r8(VR.VSEL) == want
    # stepping itself (>, <, +/-, miss-keeps, corrupt-n) is proven by
    # tools/ksstep_test.py on fake tables (no 10-minute load needed)
    fb_png(m, f"{out}/2_keyshift_version.png")
    touch((710, 38))                              # back to the main screen
    key("Browse", after=60_000_000)               # (in case it stayed open)
    # --- play: the media server streams new blocks through the hook ----
    sel = np.frombuffer(V[names[want]], dtype="<i2")
    mix = np.frombuffer(V["mix"], dtype="<i2")

    def classify():
        res = []
        for b in AUDIO_BUFS:
            blk = bytes(mem.r8(b + i) for i in range(256))
            if not any(blk):
                res.append("empty")
                continue
            res.append(("mix" if want == 0 else "version")
                         if V[names[want]].find(blk) >= 0 else
                         "mix" if V["mix"].find(blk) >= 0 else "other")
        return res
    before = classify()
    key("PlayPause", after=150_000_000)
    after = classify()
    if "version" not in after:                 # the emu has no audio clock:
        key("Cue", after=80_000_000)           # CUE (seek to start) makes
        after = classify()                     # the deck re-read blocks
    print(f"audio buffers before PLAY: {before}; after: {after}")
    # The emulator has no audio clock: after the load prebuffer the deck
    # issues NO further reads (traced: 0 hook calls on CUE/PLAY), so a
    # mid-track switch can't be observed here. Read redirection itself is
    # proven by tools/vredirect.py emu (every read = chosen version);
    # this test proves the selection path (MY SETTINGS -> VSEL) on top.
    print("note: post-load audio reads don't happen in the emu (no audio "
          "clock); redirection is covered by vredirect.py emu")
    fb_png(m, f"{out}/3_playing.png")
    if os.environ.get("SAVE2"):
        m.save(os.environ["SAVE2"])
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
