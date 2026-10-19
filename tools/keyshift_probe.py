#!/usr/bin/env python3
"""keyshift_probe.py -- REAL-TIME KEY SHIFT probe (deck, RAM-only, 1 word).

The 2026-10-09/10 emulator research (CLAUDE.md §52) proved the channel:

    SH 0x0B6C2C50 (Q20 stretch ratio)  --periodic push-->  DSP mailbox
    0x11837BC0 (cmd block word 0)      --> DSP channel object 0x1185DD88+76
    DSP 0x11837BC4 bit 0x40000000 = Master Tempo enable (toggles with MT)

Forcing 0x0B6C2C50 to Q20(1.12) in the emulator made the SH push
0x0011EB85 to the DSP within ~1 s; a normalizer loop then pulled it back
to unity (no tempo change = ratio 1). So: while MT is ON, the deck is
ONE WRITE away from a pitch change at locked tempo. If the stretcher's
ratio means "pitch factor", +2 semitones = Q20(2^(2/12)); if it is the
inverse, the sign is simply flipped -- this probe is how we find out.

Use (deck powered, USB to Mac, quit music/MIDI apps):
  1. Load a track, press PLAY, press MASTER TEMPO (light on).
  2. .venv/bin/python tools/keyshift_probe.py --semis 2
  3. LISTEN: pitch up ~2 semitones at the same tempo? note artifacts.
  4. --semis -2 : pitch down? If both directions move the same way,
     the ratio is inverted -- report and use 1/2^(k/12) (this tool's
     --inverse flips it for you).
  5. --off : back to unity (or just power-cycle).

The write is one 32-bit word into the MT tempo block -- the same class
of RAM write as every other mod here (BW shell, verified reads back).
The deck's own loop normalizes it back in UNDER A SECOND (emulator-
measured), so each write is a short audible burst: listen hard right
after each "write" line. A keeper mod hooks the MT ratio module instead
(0x8D36xxx cluster, see CLAUDE.md §53) -- next step, same technique as
perform_pads.

"""
import argparse
import sys
import time

sys.path.insert(0, "tools")
from xdj700_usb_shell import PIONEER_VID, open_deck, drain, drain_values

RATIO = 0x0B6C2C50     # Q20 stretch ratio (SH side, MT tempo block)
DIRTY = 0x0B6C2C48     # tempo-dirty flag next to it


def q20(x):
    return int(round(x * (1 << 20))) & 0xFFFFFFFF


def wr(h, addr, val):
    h.write(f"{addr:x},{val:x}BW\r\x00".encode())
    return drain(h, 2.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--semis", type=float, help="semitones to shift (±8)")
    g.add_argument("--off", action="store_true", help="write unity ratio")
    g.add_argument("--status", action="store_true",
                   help="read ratio/MT/transport words, write nothing")
    ap.add_argument("--inverse", action="store_true",
                    help="use 1/2^(k/12) instead of 2^(k/12)")
    ap.add_argument("--repeat", type=int, default=3,
                    help="writes, one per pacing window (default 3)")
    a = ap.parse_args()

    if a.status:
        # one paced read-only session: the four words that say which link
        # in the chain is live. Uses 0BR single-byte reads (verified form).
        from shellbyte import read_byte

        def rd32(h, addr):
            b = [int(read_byte(h, addr + i)[0], 16) for i in range(4)]
            return b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)

        h = open_deck(PIONEER_VID, 0x0008, 0x01)
        try:
            drain(h, 1.0)
            h.write(b"$CDJ\r\x00")
            drain(h, 1.5)
            for name, addr in (("ratio Q20", RATIO),
                               ("MT state (1=on)", 0x0B6C4308),
                               ("transport (5=play)", 0x0B6C4264),
                               ("derived-MT", 0x0B6C3C14)):
                print(f"{name:20s} @{addr:#x} = {rd32(h, addr):#010x}", flush=True)
                time.sleep(1.0)
            print(f"unity = {q20(1.0):#010x}; a held shift keeps its Q20 value")
        finally:
            h.close()
        return 0

    if a.off:
        factor, val = 1.0, q20(1.0)
    else:
        if not (-8.0 <= a.semis <= 8.0):
            sys.exit("semis range is -8..+8")
        factor = 2.0 ** (a.semis / 12.0)
        if a.inverse:
            factor = 1.0 / factor
        val = q20(factor)

    print(f"ratio factor {factor:.5f} -> Q20 {val:#010x} at {RATIO:#x}")
    print("deck must be PLAYING with MASTER TEMPO ON; listen after each write")
    h = open_deck(PIONEER_VID, 0x0008, 0x01)
    try:
        for i in range(max(1, a.repeat)):
            print(f"write {i + 1}/{a.repeat} ...", flush=True)
            print("  reply:", wr(h, RATIO, val) or "(none)")
            wr(h, DIRTY, 1)
            time.sleep(2.0)          # inside one open session: no re-handshake
        # read back
        h.write(f"{RATIO:x},4BR\r\x00".encode())
        got = drain_values(h, 2.0)
        print(f"read back first words: {[hex(v) for v in got[:4]]}")
        print("(unity {:#010x} = the deck normalized it back)".format(q20(1.0)))
    finally:
        h.close()
    print("done. power-cycle the deck any time to go fully stock.")


if __name__ == "__main__":
    main()
