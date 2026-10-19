#!/usr/bin/env python3
"""brake_length_apply.py -- adjustable VINYL BRAKE length (deck, RAM only).

How the stock brake works (emulator-proven, CLAUDE.md §120): with VINYL
BRAKE on, PAUSE makes the MAIN CPU (not the audio chip) ramp the speed
word it sends to the DSP (0x0B6C2C50 -> mailbox 0x11837BC0) linearly from
the playing speed to 0. The ramp length in timer ticks is computed once
per stop by FUN_08D36F60:

    N = K / trunc(U * (pitch + 10000) / 10000)      K = 6,000,000 (stock)

U = a u16 the firmware refreshes constantly (12605 in the emulator), so
the clean knob is K: ONE 32-bit literal at 0x08D3714C (single user,
mov.l at 0x08D37024; FUN_08D36F60 has one caller). Brake length scales
linearly with K (emulator: K x0.5 -> 40 ms, stock 90 ms, x2 185 ms,
x4 370 ms, x10 925 ms; vinyl START is not affected). Nothing else reads it.

This tool: one shell session, read-check (the mov.l site, the divide
helper literal, and the literal itself must be stock or a value this
tool wrote), one atomic LW write, read-back. Safe while playing (the word
is read only at the start of a brake stop). Power-cycle = stock.

  brake_length_apply.py --check            read only: show current length
  brake_length_apply.py --scale 4          4x the stock length
  brake_length_apply.py --ms 500           ~500 ms (stock ~90 ms at pitch 0)
  brake_length_apply.py --revert           back to stock
"""
import argparse
import struct
import sys

sys.path[:0] = [".", "tools"]

LIT = 0x08D3714C            # K literal (mov.l @(0x48,pc) at 0x08D37024)
SITE = 0x08D37024           # its only user: d149 mov.l @(...),r1
SITE_OP = 0xD149
UDIV_LIT = 0x08D37148       # same fn: divide helper pointer, stock 0x08E1C09C
UDIV = 0x08E1C09C
STOCK = 6_000_000
STOCK_MS = 90.0             # emulator, pitch 0 (tools/brake_probe.py)
K_MIN, K_MAX = 1_500_000, 120_000_000   # ~23 ms .. ~1.8 s


def k_for(scale):
    k = int(round(STOCK * scale))
    if not K_MIN <= k <= K_MAX:
        sys.exit(f"scale {scale} out of range ({K_MIN / STOCK:g}..{K_MAX / STOCK:g})")
    return k


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--scale", type=float)
    g.add_argument("--ms", type=float)
    g.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    want = None
    if a.scale is not None:
        want = k_for(a.scale)
    elif a.ms is not None:
        want = k_for(a.ms / STOCK_MS)
    elif a.revert:
        want = STOCK

    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    if len(c) != 1:
        sys.exit(f"found {len(c)} shell interface(s)")
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    ok = True
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)

        def rd(addr):
            return struct.unpack("<I", bytes(br(h, addr, 4)))[0]
        op = struct.unpack("<H", bytes(br(h, SITE, 2)))[0]
        udiv = rd(UDIV_LIT)
        cur = rd(LIT)
        if op != SITE_OP or udiv != UDIV:
            print(f"code not stock (site {op:#06x}, divide ptr {udiv:#x}) - REFUSED")
            return 1
        if cur != STOCK and not K_MIN <= cur <= K_MAX:
            print(f"brake constant {cur:#x} is not stock and not ours - REFUSED")
            return 1
        print(f"current: K={cur:,} = {cur / STOCK:.2f}x stock (~{STOCK_MS * cur / STOCK:.0f} ms)")
        if want is None:
            return 0
        if cur == want:
            print("already set")
        else:
            q = f"{LIT:x},{want:x}LW".encode() + b"\r\x00"
            h.write(q + b"\x00" * (26 - len(q)))
            drain(h, 0.5)
            got = rd(LIT)
            ok = got == want
            print(f"K -> {got:,} = {got / STOCK:.2f}x stock (~{STOCK_MS * got / STOCK:.0f} ms)",
                  "PASS" if ok else "FAIL - power-cycle the deck")
    finally:
        h.close()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
