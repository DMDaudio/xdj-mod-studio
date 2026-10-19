#!/usr/bin/env python3
"""vfile_emu_test.py -- FLAC as virtual WAV inside the emulated XDJ-700.

THE real end-to-end proof for the shipped FLAC layer (the old DeckVolve
black-box cave was retired 2026-10-11: no source, payload never engages --
see flac_emu_proof.py's header). This test uses today's installer layout.

From cache/emu_realusb.pkl (booted, stick not scanned): install FIRST,
then mount /tmp/flacstick and browse (types are classified at mount), HLE, C core:
  * install build/xflac/vfile.bin (tools/xflac/build_vfile.sh) at its link
    address, chain vf_read_prev to the live fread slot (vredirect safe)
  * extension table: "3g2" entry -> "flac", type 0x40B (WAV)
  * arm the USB slots: read x2, seek, tell, eof, size, close
  * open the folder and seek the highlighted row TO the FLAC by IMAGE
    (same row/band detection as browse_seek.py: one detent at a time,
    15M steps, verify the highlight moved -- knob detents merge if sent
    blind); the stock WAV parser must see it (info pane) and the layer
    must report a FLAC open
  * push = load: TRACK shows, no E-8302
Screens: screenshots/vfile_*.png
"""
import os
import struct
import sys
from types import SimpleNamespace
os.environ.setdefault("XDJ_HLE_DSP", "1")
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl
from ghosttap_emu import fb_png

OUT = os.environ.get("VFILE_DIR", "build/xflac")   # the app passes its copy
ROT = 0x0B6C7B48
SEEK = int(os.environ.get("SEEK", "1"))   # 1 = verify the row by image
ROW = int(os.environ.get("ROW", "1"))     # blind detents if SEEK=0
# extension classifier FUN @0x08BA9740.. returns FLAC/FLA = 5 (known but
# unsupported on the 700); make both return 11 (WAV): one halfword each,
# both in bra delay slots (mov #imm is legal there)
from flac_apply import TYPE_PATCH, EXT_PATCH        # deck installer = truth
SLOTS = {"vf_fread": 0x08D5FFA8, "vf_fread_direct": 0x08DDFFD4,
         "vf_seek": 0x08DE0298, "vf_tell": 0x08DE04FC, "vf_eof": 0x08DE0768,
         "vf_size": 0x08DE0F48, "vf_close": 0x08DDFD28}
STOCK = {"vf_fread_direct": 0x08C61A6C, "vf_seek": 0x08C62E1C, "vf_tell": 0x08C632F2,
         "vf_eof": 0x08C63344, "vf_size": 0x08C71124, "vf_close": 0x08C605F4}


def syms():
    s = {}
    for line in open(f"{OUT}/vfile.syms"):
        a, _, n = line.split()
        s[n.lstrip("_")] = int(a, 16)
    return s


def install(mem, s):
    """same steps the deck installer will do (minus pacing/verify)"""
    base = s["vf_entry"]
    img = open(f"{OUT}/vfile.bin", "rb").read()
    end = s["end"]
    assert all(mem.r8(a) == 0 for a in range(base, end, 64)), "cave not empty"
    for i, b in enumerate(img):
        mem.w8(base + i, b)
    for n, slot in SLOTS.items():
        cur = mem.r32(slot)
        if n == "vf_fread":
            mem.w32(s["vf_read_prev"], cur)          # vredirect or stock fread
        elif n == "vf_fread_direct":
            mem.w32(s["vf_direct_prev"], cur)        # stems engine or stock
        else:
            assert cur == STOCK[n], f"{n} slot {slot:#x} = {cur:#x}, not stock"
    for a_, (old, new) in EXT_PATCH.items():
        assert bytes(mem.r8(a_ + i) for i in range(4)) == old, f"{a_:#x} not stock"
        for i, b_ in enumerate(new):
            mem.w8(a_ + i, b_)
    for a_, (old, new) in TYPE_PATCH.items():
        assert mem.r16(a_) == old, f"{a_:#x} not stock"
        mem.w16(a_, new)
    for n, slot in SLOTS.items():                    # read slots last
        if n not in ("vf_fread", "vf_fread_direct"):
            mem.w32(slot, s[n])
    mem.w32(SLOTS["vf_fread_direct"], s["vf_fread_direct"])
    mem.w32(SLOTS["vf_fread"], s["vf_fread"])


def highlighted(path):
    """y-band of the highlighted browse row, or None (browse_seek's rule)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    for k in range(7):
        y = 107 + 30 * k
        if sum(im.getpixel((450, y + 5))) > 600:
            return k
    return None


def key(m, k, n=15_000_000):
    m.mem.dev.panel_frame = panl.build_frame(keys=[k])
    m.cpu.run(limit=3_000_000)
    m.mem.dev.panel_frame = panl.build_frame()
    m.cpu.run(limit=n)


def main():
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    s = syms()
    ink = os.environ.get("CKPT", "cache/emu_realusb.pkl")
    m = Machine.load(ink)            # booted; stick state depends on ink
    mem, dev = m.mem, m.mem.dev
    if os.environ.get("UI") == "1":      # the app's own install path
        import emu.ui as U
        stub = SimpleNamespace(status="", flac=False, usb_path="(test)")
        U.EmuApp._install_flac(stub, m)
        print("app install:", stub.status or "ok", "flag", stub.flac)
    else:
        install(mem, s)                                  # mods first, THEN the stick
    st = lambda: [mem.r32(s["vf_stats"] + 4 * i) for i in range(7)]
    u = dev._usb()
    u.unplug()
    m.cpu.run(limit=20_000_000)
    disk = open_disk(os.environ.get("STICK", "/tmp/flacstick"))
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(6):
        m.cpu.run(limit=250_000_000)
    print("stick mounted:", st())
    key(m, "Browse")
    mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)
    m.cpu.run(limit=15_000_000)
    for i in range(3):                               # [FOLDER] -> Contents -> FlacTest
        key(m, "RotaryPush", 30_000_000)
    fb_png(m, f"screenshots/vfile_{os.environ.get('KIND', 'flac')}_list.png")
    print("folder open: stats [probes, flac opens, vreads, decodes] =", st())
    if SEEK:                                         # detents until the FLAC
        target = int(os.environ.get("BAND", "1"))    # row band of the FLAC
        for _ in range(6):
            m.cpu.run(limit=40_000_000)
            fb_png(m, "/tmp/vtest_band.png")
            band = highlighted("/tmp/vtest_band.png")
            if band is None:
                print("   no highlighted row visible yet")
                break
            if band == target:
                break
            mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)
            m.cpu.run(limit=20_000_000)
            print(f"   highlight band {band} -> one detent")
        fb_png(m, "/tmp/vtest_seek.png")
        print("cursor band after seek:", highlighted("/tmp/vtest_seek.png"))
    else:
        for _ in range(ROW):                         # legacy blind stepping
            mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)
            m.cpu.run(limit=20_000_000)
    fb_png(m, f"screenshots/vfile_{os.environ.get('KIND', 'flac')}_info.png")
    print("cursor on the FLAC row:", st())
    OPS = {1: "read", 2: "seek", 3: "tell", 4: "eof", 5: "size", 6: "close", 7: "dread"}
    n = mem.r32(s["vf_logi"])
    for k in range(max(0, n - 64), n):
        e = [mem.r32(s["vf_log"] + ((k % 64) * 5 + j) * 4) for j in range(5)]
        print(f"   {OPS.get(e[0], e[0])}: fp {e[1]:#x} a {e[2]:#x} b {e[3]:#x} -> {e[4]:#x}")
    m.save("cache/vfile_on_flac.pkl")
    key(m, "RotaryPush", 120_000_000)                # load
    fb_png(m, f"screenshots/vfile_{os.environ.get('KIND', 'flac')}_loaded.png")
    print("after load:", st(), "mode", mem.r32(0x0B6C4264))
    flac_opened = st()[4 if os.environ.get("KIND") == "opus" else 1] > 0
    print("RESULT:", "PASS (layer opened the FLAC; check the screenshots)"
          if flac_opened else "FAIL (layer never opened a FLAC)")
    return 0 if flac_opened else 1


if __name__ == "__main__":
    sys.exit(main())
