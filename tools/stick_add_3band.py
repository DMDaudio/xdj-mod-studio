#!/usr/bin/env python3
"""stick_add_3band.py -- make rekordbox's 3-band data visible to an XDJ-700.

rekordbox 6+ writes the CDJ-3000 3-band waveforms (PWV6 overview,
PWV7 detail) only into ANLZ0000.2EX, which the XDJ-700 never opens.
The XDJ-700 DOES load every non-standard section of ANLZ0000.EXT into
RAM on track load (proven in the emulator, CLAUDE.md §33). So this copies
PWV6 from .2EX and appends it to .EXT (file-length header updated).
Stock firmware ignores the extra section; the waveform mod reads it.

  stick_add_3band.py STICK_ROOT [ANLZ_DIR ...]   (default: all tracks)

Idempotent (skips .EXT that already has PWV6). The original is kept once
as ANLZ0000.EXT.orig next to it (restore = rename back).
"""
import glob
import os
import shutil
import struct
import sys


def sections(b):
    if len(b) < 12:                       # missing/empty/truncated file
        return []
    hl = struct.unpack(">I", b[4:8])[0]
    p, out = hl, []
    while p + 12 <= len(b):
        tag = b[p:p + 4]
        h, t = struct.unpack(">II", b[p + 4:p + 12])
        if t == 0 or p + t > len(b):
            break
        out.append((tag, p, t))
        p += t
    return out


def patch_dir(d):
    ext, x2 = os.path.join(d, "ANLZ0000.EXT"), os.path.join(d, "ANLZ0000.2EX")
    if not (os.path.exists(ext) and os.path.exists(x2)):
        return "skip (no EXT/2EX)"
    e = open(ext, "rb").read()
    if any(t == b"PWV6" for t, _, _ in sections(e)):
        return "already has PWV6"
    x = open(x2, "rb").read()
    sec = [x[p:p + t] for tag, p, t in sections(x) if tag == b"PWV6"]
    if not sec:
        return "skip (no PWV6 in 2EX)"
    if not os.path.exists(ext + ".orig"):
        shutil.copy2(ext, ext + ".orig")
    end = sum(t for _, _, t in sections(e)) + struct.unpack(">I", e[4:8])[0]
    new = bytearray(e[:end]) + sec[0]
    struct.pack_into(">I", new, 8, len(new))      # PMAI total file length
    tmp = ext + ".tmp"
    open(tmp, "wb").write(new)
    assert any(t == b"PWV6" for t, _, _ in sections(bytes(new)))
    os.replace(tmp, ext)
    return f"added PWV6 ({len(sec[0])} B)"


def main():
    root = sys.argv[1]
    dirs = sys.argv[2:] or sorted(glob.glob(
        os.path.join(root, "PIONEER", "USBANLZ", "*", "*")))
    for d in dirs:
        if not os.path.isabs(d):
            d = os.path.join(root, "PIONEER", "USBANLZ", d)
        print(os.path.relpath(d, root), "->", patch_dir(d))


if __name__ == "__main__":
    main()
