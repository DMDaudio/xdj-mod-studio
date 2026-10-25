#!/usr/bin/env python3
"""slotscan.py -- static delay-slot check for our SH-4 blobs (CLAUDE.md 119:
the BEAT JUMP freeze was a PC-relative load in a bsr delay slot; real
silicon raises a slot-illegal exception, the emulator does not).

  .venv/bin/python tools/slotscan.py            # perform_pads code units

Flags every delayed branch (bra bsr braf bsrf jmp jsr rts rte bt/s bf/s)
whose slot holds a branch, a PC-relative load (mov.w/mov.l @(d,pc), mova)
or trapa. Data words that merely LOOK like branches (pools, glyphs) show
up as hits too: check each hit's address against the unit's code range."""
import struct
import sys

sys.path[:0] = [".", "tools"]


def is_delayed(w):
    t = w >> 12
    if t in (0xA, 0xB):                       # bra, bsr
        return True
    if (w & 0xFF00) in (0x8D00, 0x8F00):      # bt/s, bf/s
        return True
    if (w & 0xF0FF) in (0x0023, 0x0003, 0x402B, 0x400B):  # braf bsrf jmp jsr
        return True
    return w in (0x000B, 0x002B)              # rts, rte


def is_illegal_slot(w):
    if is_delayed(w) or (w & 0xFF00) in (0x8900, 0x8B00):   # also bt/bf
        return True
    t = w >> 12
    if t in (0x9, 0xD) or (w & 0xFF00) in (0xC700, 0xC300):  # pc-rel, mova, trapa
        return True
    return False


def scan(base, blob, lo=0, hi=None):
    hits = []
    hi = len(blob) if hi is None else hi
    for off in range(lo, hi - 2, 2):
        w = struct.unpack_from("<H", blob, off)[0]
        s = struct.unpack_from("<H", blob, off + 2)[0]
        if is_delayed(w) and is_illegal_slot(s):
            hits.append((base + off, w, s))
    return hits


if __name__ == "__main__":
    import perform_pads as P
    from shasm import Asm
    code = {P.PC0: "HOOK_SRC", P.KSSTEP: "KSSTEP_SRC", P.HCSW: "HCSW_SRC",
            P.RTKEEP: "RTKEEP_SRC", P.TRKKEY: "TRKKEY_SRC"}
    bad = 0
    for a, blob in P.blobs():
        if a not in code and a not in (P.PDRAW, P.KSDRAW):
            continue
        hits = scan(a, blob)
        for h in hits:
            print(f"  {h[0]:#x}: {h[1]:04x} / slot {h[2]:04x}")
        print(f"{a:#x} ({len(blob)} B): {len(hits)} hit(s)")
        bad += len(hits)
    print("hits to inspect:", bad)
