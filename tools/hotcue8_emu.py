#!/usr/bin/env python3
"""hotcue8_emu.py -- does the XDJ-700 keep hot cues D-H when it loads a
track? (emulator, no deck, the user's library untouched)

Builds a mini stick in the scratch folder: the real library database
files (copied), ONE real track (symlinked) and copies of its analysis
files with hot cues A-H written in rekordbox's own format (PCOB/PCPT in
.DAT, PCOB + PCO2/PCP2 in .EXT; layouts per Deep-Symmetry's
crate-digger docs). Mounts it, browses to the track, loads it, then
searches all RAM for the eight cue times (ms and the deck's 1/75 s and
1/150 s units) to see how many cues the player keeps and where.

Run: .venv/bin/python tools/hotcue8_emu.py [STICKDIR]
"""
import os
import shutil
import struct
import sys

os.environ.setdefault("XDJ_HLE_DSP", "1")
sys.path[:0] = [".", "tools"]
import numpy as np                       # noqa: E402

LIB = os.path.expanduser("~/Downloads/Rekordbox stuff")
ANLZ = "PIONEER/USBANLZ/P000/0002D83A"
TRACK = ("Contents/Aaron Carl/The Greatest House Acappellas/"
         "aaron_carl_-_house_music-sq.mp3")
TIMES = [5000 + 8000 * i for i in range(8)]          # A..H in ms
ROT = 0x0B6C7B48


def pcpt(n, ms):
    return (b"PCPT" + struct.pack(">II", 0x1C, 0x38) +
            struct.pack(">IIIHH", n, 4, 0x10000, 0xFFFF, 0xFFFF) +
            struct.pack(">BBHII", 1, 0, 0x3E8, ms, 0xFFFFFFFF) + bytes(16))


def pcp2(n, ms):
    body = (struct.pack(">I", n) + struct.pack(">B3x", 1) +
            struct.pack(">II", ms, 0xFFFFFFFF) + struct.pack(">B7x", n) +
            struct.pack(">HHI", 0, 0, 0) + bytes([n, 0xFF, 0x40, 0x40]))
    out = b"PCP2" + struct.pack(">II", 0x10, 0x58) + body
    return out.ljust(0x58, b"\0")


def rewrite(path, kind):
    """replace the empty hot-cue PCOB (type 1) [+ PCO2 type 1 in .EXT]"""
    d = bytearray(open(path, "rb").read())
    hl = struct.unpack(">I", d[4:8])[0]
    out = bytearray(d[:hl])
    p = hl
    while p + 12 <= len(d):
        tag = bytes(d[p:p + 4])
        lt = struct.unpack(">I", d[p + 8:p + 12])[0]
        sec = d[p:p + lt]
        typ = struct.unpack(">I", sec[12:16])[0] if tag in (b"PCOB", b"PCO2") else None
        if tag == b"PCOB" and typ == 1:
            ents = b"".join(pcpt(i + 1, t) for i, t in enumerate(TIMES))
            sec = (b"PCOB" + struct.pack(">II", 0x18, 0x18 + len(ents)) +
                   struct.pack(">IHHI", 1, 0, len(TIMES), 0xFFFFFFFF) + ents)
        elif tag == b"PCO2" and typ == 1:
            ents = b"".join(pcp2(i + 1, t) for i, t in enumerate(TIMES))
            sec = (b"PCO2" + struct.pack(">II", 0x14, 0x14 + len(ents)) +
                   struct.pack(">IHH", 1, len(TIMES), 0) + ents)
        out += sec
        p += lt
    struct.pack_into(">I", out, 8, len(out))
    open(path, "wb").write(out)


def build(stick):
    if os.path.exists(stick):
        shutil.rmtree(stick)
    os.makedirs(os.path.dirname(os.path.join(stick, TRACK)))
    os.symlink(os.path.join(LIB, TRACK), os.path.join(stick, TRACK))
    for rel in ("PIONEER/rekordbox", "PIONEER/USBANLZ/USBMNG.DAT",
                "PIONEER/MYSETTING.DAT", "PIONEER/MYSETTING2.DAT",
                "PIONEER/DEVSETTING.DAT", "PIONEER/DJMMYSETTING.DAT",
                "PIONEER/djprofile.nxs", ANLZ):
        src, dst = os.path.join(LIB, rel), os.path.join(stick, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
    for fn, kind in (("ANLZ0000.DAT", "dat"), ("ANLZ0000.EXT", "ext")):
        rewrite(os.path.join(stick, ANLZ, fn), kind)


def find_times(m):
    hits = {}
    for nm, base, ram in (("ram0", 0x08000000, m.mem.ram0),
                          ("ram1", 0x0C000000, m.mem.ram1)):
        a = np.frombuffer(bytes(ram), np.uint8)
        for unit, conv in (("ms", lambda t: t), ("1/75s", lambda t: t * 75 // 1000),
                           ("1/150s", lambda t: t * 150 // 1000)):
            for i, t in enumerate(TIMES):
                v = conv(t)
                for fmt in ("<u4", ">u4"):
                    pat = np.array([v], fmt).tobytes()
                    idx = []
                    pos = bytes(ram).find(pat)
                    while pos >= 0 and len(idx) < 50:
                        idx.append(base + pos)
                        pos = bytes(ram).find(pat, pos + 1)
                    hits[(unit, fmt, i)] = hits.get((unit, fmt, i), []) + idx
    return hits


def main():
    from emu.machine import Machine
    from emu import panl
    from emu.usbdisk import open_disk
    from emu.usbhost import MassStorage
    from ghosttap_emu import fb_png
    stick = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "hotcue8stick")
    build(stick)
    print("mini stick:", stick)
    m = Machine.load("cache/emu_realusb.pkl")
    mem, dev = m.mem, m.mem.dev

    def key(k, n=15_000_000):
        dev.panel_frame = panl.build_frame(keys=[k])
        m.cpu.run(limit=3_000_000)
        dev.panel_frame = panl.build_frame()
        m.cpu.run(limit=n)

    u = dev._usb()
    u.unplug()
    m.cpu.run(limit=20_000_000)
    disk = open_disk(stick)
    dev.usb_disk = disk
    u.stick = MassStorage(disk)
    for _ in range(6):
        m.cpu.run(limit=250_000_000)
    key("Browse")
    mem.w16(ROT, (mem.r16(ROT) + 1) & 0xFFFF)
    m.cpu.run(limit=15_000_000)
    for _ in range(4):                    # [FOLDER] Contents artist album
        key("RotaryPush", 30_000_000)
    fb_png(m, "screenshots/hotcue8_list.png")
    key("RotaryPush", 400_000_000)        # load the (only) track
    m.cpu.run(limit=300_000_000)
    fb_png(m, "screenshots/hotcue8_loaded.png")
    hits = find_times(m)
    found = {}
    for (unit, fmt, i), idx in hits.items():
        for a in idx:
            found.setdefault((unit, fmt), {}).setdefault(i, []).append(a)
    # cue tables: places where A, B, C... sit at a constant stride
    for (unit, fmt), per in sorted(found.items()):
        if 0 not in per:
            continue
        for a0 in per[0]:
            for stride in range(4, 0x200, 4):
                n = 1
                while n < 8 and (a0 + n * stride) in per.get(n, []):
                    n += 1
                if n >= 3:
                    print(f"cue table? {unit} {fmt} @ {a0:#010x} stride {stride}: "
                          f"{n} of 8 cues in a row")
    print("cues found anywhere (unit, fmt): count of A..H present ->",
          {k: sorted(v) for k, v in found.items() if len(v) >= 3})
    m.save(os.path.join(os.path.dirname(stick), "hotcue8_loaded.pkl"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
