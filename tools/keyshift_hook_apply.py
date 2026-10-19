#!/usr/bin/env python3
"""keyshift_hook_apply.py [--revert] [--semis K] [--inverse] -- keeper hook.

Real-time key shift = own the Q20 stretch-ratio word 0x0B6C2C50 while MT
is on (HANDOFF_2026-10-10.md section 1-2). The SH's own loop recomputes
that word back to unity in < 0.5 s, so the deck recipe is:

  install: NOP the 7 ratio stores (14 bytes) + write the word once (no dirty)
  revert:  restore the 7 original instructions (14 bytes)

Emu proof: tools/keyshift_hook_test.py HOOK TEST PASS (word==ours 50/50,
mailbox echo==ours 50/50 over 10 s emulated, transport stable mode 5).

RAM only: reboot restores stock. ONE shell open per run (session budget).
NEVER writes the dirty flag 0x0B6C2C48: constant re-dirty STALLS the SH
push machine (0% duty, emu-proven); dirty-on-change is an emu-only
write-fight, not needed once the hook is in. The SH itself pushes our
value to DSP mailbox 0x11837BC0.

Write order (tear safety, capstone-verified):
  apply:  low byte first  (mid-insn = AND r0,rN, harmless if executed once)
  revert: high byte first (same reason; the other order hits illegal slots
          0x0062/0x0052 on two sites -> possible exception)

Usage:
  play a track, MT on, then:
    keyshift_hook_apply.py --semis 2    # install hook + shift +2 semitones
    keyshift_hook_apply.py --semis -2   # change to -2 (hook already live)
    keyshift_hook_apply.py --revert     # restore stock code
    keyshift_hook_apply.py --revert --semis 0  # + write unity ratio
  --inverse uses 1/2^(k/12) (direction still open; see keyshift_probe.py).
"""
import argparse
import sys

sys.path.insert(0, "tools")
import hid
from shellbyte import cmd, read_byte
from xdj700_usb_shell import open_deck, drain, PIONEER_VID

# (addr_low, orig_lo, orig_hi): low byte lives at addr_low (LE), high next.
SITES = [
    (0x08D3664E, 0x42, 0x27),  # mov.l r4,@r7
    (0x08D365B0, 0x62, 0x27),  # mov.l r6,@r7
    (0x08D366B2, 0x52, 0x27),  # mov.l r5,@r7
    (0x08D366C0, 0x62, 0x27),  # mov.l r6,@r7  (stores const 0x00080000)
    (0x08D36788, 0x22, 0x27),  # mov.l r2,@r7
    (0x08D36960, 0x22, 0x27),  # mov.l r2,@r7  (delay slot, gated [3EB4]==1)
    (0x08D3737C, 0x02, 0x25),  # mov.l r0,@r5
]
NOP_LO, NOP_HI = 0x09, 0x00

RATIO = 0x0B6C2C50  # Q20 stretch ratio, LE 4 bytes


def q20(factor):
    return int(round(factor * (1 << 20))) & 0xFFFFFFFF


def put(h, addr, accept, new):
    """Fail-stop single-byte write; accept = live values tolerated."""
    got, _ = read_byte(h, addr)
    if got == f"{new:02x}":
        print(f"{addr:#x}: already {new:02x}, skipped")
        return
    if got not in {f"{a:02x}" for a in accept}:
        sys.exit(f"FAIL at {addr:#x}: live={got}, expected one of "
                 f"{[f'{a:02x}' for a in accept]} - aborting (RAM-only; "
                 f"reboot clears anything already written)")
    cmd(h, f"{addr:x},{new:02x}BW\r\x00".encode(), f"write {addr:#x}", 2.0)
    got2, _ = read_byte(h, addr)
    if got2 != f"{new:02x}":
        sys.exit(f"FAIL at {addr:#x}: re-read={got2}, expected {new:02x}")
    print(f"{addr:#x}: {got} -> {new:02x} verified")


def put_ratio(h, val):
    """Write the 32-bit ratio LE, byte-wise, verify each (no orig check:
    the normalizer owns this word live, so any live value is accepted)."""
    for i in range(4):
        b = (val >> (8 * i)) & 0xFF
        addr = RATIO + i
        got, _ = read_byte(h, addr)
        if got == f"{b:02x}":
            print(f"{addr:#x}: already {b:02x}, skipped")
            continue
        cmd(h, f"{addr:x},{b:02x}BW\r\x00".encode(), f"write {addr:#x}", 2.0)
        got2, _ = read_byte(h, addr)
        if got2 != f"{b:02x}":
            sys.exit(f"FAIL at {addr:#x}: re-read={got2}, expected {b:02x}")
        print(f"{addr:#x}: {got} -> {b:02x} verified")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--revert", action="store_true",
                    help="restore stock instructions")
    ap.add_argument("--semis", type=float, default=None,
                    help="semitones to shift (-8..+8); writes Q20 once")
    ap.add_argument("--inverse", action="store_true",
                    help="use 1/2^(k/12) instead of 2^(k/12)")
    a = ap.parse_args()

    val = None
    if a.semis is not None:
        if not (-8.0 <= a.semis <= 8.0):
            sys.exit("semis range is -8..+8")
        factor = 2.0 ** (a.semis / 12.0)
        if a.inverse:
            factor = 1.0 / factor
        val = q20(factor)
        print(f"ratio factor {factor:.5f} -> Q20 {val:#010x} at {RATIO:#x}")

    # Build the ordered byte plan BEFORE touching the deck (bad args must
    # never cost a paced shell session). Each entry: (addr, accept, new).
    plan = []
    for base, olo, ohi in SITES:
        if a.revert:
            # high byte first (safe intermediate AND, see module docstring)
            plan.append((base + 1, [NOP_HI], ohi))
            plan.append((base, [NOP_LO], olo))
        else:
            # low byte first (safe intermediate AND)
            plan.append((base, [olo], NOP_LO))
            plan.append((base + 1, [ohi], NOP_HI))
    assert len(plan) == 14, f"expected 14 hook bytes, got {len(plan)}"

    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power "
                 f"and quit music/MIDI apps")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        cmd(h, b"$CDJ\r\x00", "handshake reply", 1.5)
        mode = "REVERT (keeper -> stock)" if a.revert else \
            "APPLY (NOP the 7 ratio stores)"
        print(f"--- {mode}: {len(plan)} bytes ---")
        for addr, accept, new in plan:
            put(h, addr, accept, new)
        if val is not None:
            print(f"--- ratio -> Q20 {val:#010x} ({a.semis:+g} semis"
                  f"{', inverse' if a.inverse else ''}; NO dirty write) ---")
            put_ratio(h, val)
        print("RESULT: PASS -",
              "stock ratio loop restored (reboot also clears)" if a.revert
              else "keeper live. MT on + playing: pitch should hold. "
                   "Change key with --semis K (no reinstall needed).")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
