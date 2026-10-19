#!/usr/bin/env python3
"""selector_apply.py [--revert] -- re-point RELOOP/EXIT to the waveform
style selector on the DECK (8 bytes, fail-stop per byte, one short
session; emulator PASS: tools/selector.py emu). Reboot = stock."""
import sys
sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from selector import plan_bytes


def show(tag, raw):
    print(f"{tag}: {raw[:120]!r}")


def cmd(h, payload, tag, secs=2.0):
    print(f"--> {payload!r}")
    h.write(payload + b"\x00" * (26 - len(payload)))
    raw = drain(h, secs)
    show(tag, raw)
    return raw


def _rb(h, addr):
    raw = cmd(h, f"{addr:x},0BR".encode(), f"read {addr:#x}", 1.5)
    return raw.split()[0] if raw.split() else "?"


def put(h, addr, accept, new):
    from rgb_overview_apply import bw
    got = _rb(h, addr)
    if got == f"{new:02x}":
        print(f"{addr:#x}: already {new:02x}, skipped")
        return
    if got not in {f"{a:02x}" for a in accept}:
        sys.exit(f"FAIL at {addr:#x}: live={got}, expected one of "
                 f"{[f'{a:02x}' for a in accept]} - aborting (RAM-only; "
                 f"reboot clears anything already written)")
    bw(h, addr, new)
    got2 = _rb(h, addr)
    if got2 != f"{new:02x}":
        sys.exit(f"FAIL at {addr:#x}: re-read={got2}, expected {new:02x}")
    print(f"{addr:#x}: {got} -> {new:02x} verified")


def main():
    revert = sys.argv[1:] == ["--revert"]
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        cmd(h, b"$CDJ\r\x00", "handshake reply", 1.5)
        for a, old, new in plan_bytes():
            put(h, a, [new] if revert else [old], old if revert else new)
        print("RESULT: PASS -", "RELOOP/EXIT restored" if revert else
              "RELOOP/EXIT now cycles blue -> rgb -> 3band")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
