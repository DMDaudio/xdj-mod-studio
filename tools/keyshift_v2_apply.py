#!/usr/bin/env python3
"""keyshift_v2_apply.py [--slot pitch|tempo] [--semis K] [--inverse] [--revert]

Key shift v2: own ONE DSP mailbox slot with a single 2-byte NOP.

How the SH feeds the DSP command block (traced 2026-10-11, emu, MT on):
  0x0B6C2C50 (tempo word) --0x08D36B34 mov.l r13,@(4,r12)--> 0x09D24DC8
      --> stack block --> mailbox 0x11837BC0 (BC0)
  0x0B6C3E9C (poller copy) --0x08D36B3A mov.l r1,@(20,r12)--> 0x09D24DD8
      --> stack block --> mailbox 0x11837BD8 (BD8)
Normally both carry the tempo value, which is why deck session 4 (7-NOP
hook, word 2C50 = 1.12) gave FASTER music with the key unchanged.

  --slot pitch (default): NOP 0x08D36B3A, write Q20 to 0x09D24DD8 -> BD8
       differs from BC0, tempo path untouched (fader keeps working).
  --slot tempo:           NOP 0x08D36B34, write Q20 to 0x09D24DC8 -> BC0
       differs from BD8 (control experiment; freezes the fader's BC0).

Emulator (stub DSP, so mailbox only): both variants hold 10/10 with the
other slot at unity and playback running. WHICH slot moves the key on the
real DSP is the question this tool answers -- by ear.

Tear safety: the NOP goes in with ONE atomic 16-bit WW store (byte-wise
would briefly execute a stray store: 0x1C09 / 0x0015). WW has never run
on hardware, so it is self-tested first on a surveyed-zero scratch word
inside our own perform-pads cave (must read 0000; restored to 0000).

RAM only; reboot restores stock. ONE shell session per run.
Usage (track playing, MASTER TEMPO ON):
  keyshift_v2_apply.py --semis 2            # pitch slot, +2 semitones
  keyshift_v2_apply.py --semis -2           # change value (hook stays)
  keyshift_v2_apply.py --slot tempo --semis 2
  keyshift_v2_apply.py --revert [--slot ...]
"""
import argparse
import sys

sys.path.insert(0, "tools")

VARIANTS = {   # slot: (insn addr, stock halfword, target word)
    "pitch": (0x08D36B3A, 0x1C15, 0x09D24DD8),
    "tempo": (0x08D36B34, 0x1CD1, 0x09D24DC8),
}
NOP = 0x0009
SCRATCH = 0x081DFFF0      # inside PC0..PEND (perform pads), surveyed zero


def q20(semis, inverse=False):
    f = 2.0 ** (semis / 12.0)
    return int(round((1.0 / f if inverse else f) * (1 << 20))) & 0xFFFFFFFF


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slot", choices=VARIANTS, default="pitch")
    ap.add_argument("--semis", type=float)
    ap.add_argument("--inverse", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    if a.semis is not None and not -8 <= a.semis <= 8:
        sys.exit("semis range is -8..+8")
    if a.semis is None and not a.revert:
        sys.exit("give --semis K (or --revert)")
    site, stock, target = VARIANTS[a.slot]
    val = None if a.semis is None else q20(a.semis, a.inverse)

    import hid
    from shellbyte import cmd, read_byte
    from xdj700_usb_shell import open_deck, drain, PIONEER_VID

    def rd16(h, addr):
        lo, _ = read_byte(h, addr)
        hi, _ = read_byte(h, addr + 1)
        return int(hi + lo, 16)

    def ww(h, addr, v):
        cmd(h, f"{addr:x},{v:x}WW\r\x00".encode(), f"WW {addr:#x}", 2.0)
        got = rd16(h, addr)
        if got != v:
            sys.exit(f"FAIL: WW {addr:#x} reads {got:04x}, wanted {v:04x} "
                     f"- stop and power-cycle the deck")

    def put32(h, addr, v):
        for i in range(4):
            b = (v >> (8 * i)) & 0xFF
            cmd(h, f"{addr + i:x},{b:02x}BW\r\x00".encode(), "BW", 2.0)
            got, _ = read_byte(h, addr + i)
            if got != f"{b:02x}":
                sys.exit(f"FAIL at {addr + i:#x}: re-read {got}, wanted {b:02x}")

    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power "
                 f"and quit music/MIDI apps")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        cmd(h, b"$CDJ\r\x00", "handshake reply", 1.5)
        live = rd16(h, site)
        want_from = NOP if a.revert else stock
        want_to = stock if a.revert else NOP
        if live == want_to:
            print(f"{site:#x}: already {want_to:04x}")
        elif live != want_from:
            sys.exit(f"FAIL: {site:#x} live {live:04x}, expected {want_from:04x} "
                     f"(wrong firmware or other hook live) - nothing written")
        else:
            if rd16(h, SCRATCH) != 0:
                sys.exit(f"FAIL: scratch {SCRATCH:#x} not zero - nothing written")
            ww(h, SCRATCH, 0xA55A)
            ww(h, SCRATCH, 0x0000)
            print("WW self-test OK (atomic 16-bit write works)")
            ww(h, site, want_to)
            print(f"{site:#x}: {live:04x} -> {want_to:04x} verified")
        if val is not None:
            put32(h, target, val)
            print(f"{target:#x} <- Q20 {val:#010x} ({a.semis:+g} semis"
                  f"{', inverse' if a.inverse else ''}) verified")
        print("RESULT: PASS -", "stock restored" if a.revert else
              f"{a.slot} slot owned. Listen: key moved? tempo moved? both?")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
