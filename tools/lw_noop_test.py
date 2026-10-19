#!/usr/bin/env python3
"""lw_noop_test.py -- prove the 32-bit USB write path with zero state change.

Shape:
  1. handshake + ?V (confirm shell alive)
  2. BR read 16 bytes at a 4-aligned site  -> reply A
  3. BR read the 4 target bytes             -> value V (u32)
  4. LW write V back to the same address    (no-op: deck ends as it started)
  5. BR read 16 bytes at the site again     -> reply B
  6. PASS iff A == B (and the word reads match too)

If this passes on the deck, bulk installers (~4 KB of cave blobs) can use
one LW per 4 bytes instead of one BW per byte (~4x fewer shell rounds).
If it fails or misbehaves, everything stays on byte writes; nothing here
can brick anything (one aligned word written with its own current value,
inside the RAM window; flash commands do not exist in this script).

Usage: .venv/bin/python tools/lw_noop_test.py [ADDR]
Default site: 0x08D51C0C (4-aligned word containing the OUT->Fwd4 patch
byte; live value read back first, never assumed).
"""
import sys
sys.path.insert(0, "tools")
import time
from xdj700_usb_shell import open_deck, drain, show, PIONEER_VID
import hid

SITE = 0x08D51C0C


def cmd(h, payload, tag, secs=2.0):
    print(f"--> {payload!r}")
    h.write(payload + b"\x00" * (26 - len(payload)))
    raw = drain(h, secs)
    show(tag, raw)
    return raw


def words(raw):
    toks = raw.decode("ascii", "replace").split()
    return [t.lower() for t in toks if len(t) == 2 and all(
        c in "0123456789abcdefABCDEF" for c in t)]


def main():
    addr = int(sys.argv[1], 16) if len(sys.argv) > 1 else SITE
    if not (0x08000000 <= addr < 0x10000000):
        sys.exit("ADDR must be in RAM window 0x08000000-0x0FFFFFFF")
    if addr & 3:
        sys.exit(f"ADDR {addr:#x} not 4-aligned (pick an aligned site)")

    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        print("draining pre-existing traffic...")
        drain(h, 1.0)
        cmd(h, b"$CDJ\r\x00", "handshake reply", 1.5)
        cmd(h, b"?V\r\x00", "version reply", 1.5)

        before16 = cmd(h, f"{addr:x},fBR\r\x00".encode(), "site before", 2.0)
        single1 = cmd(h, f"{addr:x},3BR\r\x00".encode(), "target word", 2.0)

        # last 4 hex-byte tokens of the word read, little-endian wire order
        hx = words(single1)
        if len(hx) < 4:
            sys.exit("FAIL: could not parse target word from reply - "
                     "aborting before any write")
        hx = hx[-4:]
        # consistency gate: the same 4 bytes must lead the independent
        # 16-byte window read (same ascending wire order). A mismatch means
        # echo/prompt contamination, not deck data - abort the write.
        hx16 = words(before16)
        if len(hx16) < 4 or hx16[:4] != hx:
            sys.exit("FAIL: word read disagrees with window read "
                     f"({hx} vs {hx16[:4]}) - aborting before any write")
        # shell takes the u32 value; wire shows bytes low-first, so the
        # value is the reverse-digit string of the four tokens
        val = int("".join(hx[::-1]), 16)
        print(f"target {addr:#x} reads {' '.join(hx)} "
              f"(= {val:#010x}); writing it back unchanged...")

        cmd(h, f"{addr:x},{val:x}LW\r\x00".encode(), "write reply", 2.0)
        time.sleep(0.3)

        single2 = cmd(h, f"{addr:x},3BR\r\x00".encode(), "target re-read", 2.0)
        after16 = cmd(h, f"{addr:x},fBR\r\x00".encode(), "site after", 2.0)

        ok = (before16 == after16) and (single1 == single2)
        print("RESULT:", "PASS - deck unchanged, 32-bit write path proven"
              if ok else "FAIL - replies differ, stay on byte writes")
        return 0 if ok else 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
