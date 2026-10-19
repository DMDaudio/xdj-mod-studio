#!/usr/bin/env python3
"""session_probe.py -- ONE shell open, handshake + several RAM reads inside.

Read-only. Windows (addr, count): beat-jump patch immediates (all four
parser variants), touch panel struct (payload/slots/flags), screen mode
word *0x0B6C4264, beat-jump guard *0x0B310424. All inside the RAM window.

Usage: .venv/bin/python tools/session_probe.py
"""
import re
import sys
import time

import hid

sys.path.insert(0, "tools")
from xdj700_usb_shell import PIONEER_VID, open_deck, drain

# (label, addr, count) -- count = bytes, 1..32 (BR masks count-1 to 5 bits)
WINDOWS = [
    ("patch out-v1+in-v1 imms", 0x8D51C0E, 28),
    ("patch out-v2+in-v2 imms", 0x8D51D3A, 28),
    ("touch struct +00..+1f", 0x0B6C7A28, 32),
    ("touch struct +20..+3f", 0x0B6C7A48, 32),
    ("touch struct +40..+5f", 0x0B6C7A68, 32),
    ("touch struct +60..+7f", 0x0B6C7A88, 32),
    ("screen mode *0x0B6C4264", 0x0B6C4264, 4),
    ("bj guard *0x0B310424", 0x0B310424, 4),
]

EXPECTED_PATCH = {  # stock immediates (and, or, key) per variant
    "out-v1": ("FD", "02", "56"), "in-v1": ("FB", "04", "56"),
    "out-v2": ("FD", "02", "56"), "in-v2": ("FB", "04", "56"),
}


def parse_br_reply(raw: bytes):
    """BR replies are ASCII 'XX ' per value across 16-byte reports (+CRLF)."""
    txt = raw.decode("ascii", "replace")
    vals = [v for v in re.findall(r"\b[0-9A-Fa-f]{2}\b(?=\s|$)", txt)]
    return vals


def main():
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    results = {}
    import time as _t
    fn = f"/tmp/session_probe_{int(_t.time())}.log"
    log = open(fn, "w")
    print(f"raw log: {fn}", flush=True)
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        hs = drain(h, 1.5)
        print(f"handshake reply: {hs[:32]!r}", flush=True)
        for label, addr, count in WINDOWS:
            if not (0x08000000 <= addr < 0x10000000):
                sys.exit(f"{addr:#x} outside RAM window - refusing")
            q = f"{addr:x},{count - 1:x}BR\r\x00".encode()
            h.write(q + b"\x00" * (26 - len(q)))
            # 32-value replies trickle across slow 16B reports: long drains
            # (touch_probe learned 2s truncates to nothing)
            raw = drain(h, 9.0 if count > 8 else 3.0)
            log.write(f"--- {label} @{addr:08x} ---\n"
                      + raw.decode("ascii", "replace") + "\n")
            log.flush()
            vals = parse_br_reply(raw)
            results[label] = (addr, vals)
            hexs = " ".join(vals)
            print(f"{label} @ {addr:08x} ({count}B): {hexs}"
                  + ("" if len(vals) >= count else
                     f"   [short: got {len(vals)}/{count}]"), flush=True)
            time.sleep(0.3)
    finally:
        h.close()
        log.close()

    # verdict on patch sites
    print("\n-- patch-site check (stock = and,or,key immediates) --")
    offs = {"out-v1": (1, 3, 7), "in-v1": (19, 21, 25),
            "out-v2": (1, 3, 7), "in-v2": (19, 21, 25)}
    for widx, vname in ((0, "out-v1"), (0, "in-v1"), (1, "out-v2"), (1, "in-v2")):
        label, addr, count = WINDOWS[widx]
        vals = results[label][1]
        if len(vals) < count:
            print(f"{vname}: incomplete read, no verdict")
            continue
        a, o, k = (vals[i] for i in offs[vname])
        exp = EXPECTED_PATCH[vname]
        state = "STOCK" if (a, o, k) == exp else f"PATCHED/OTHER (exp {' '.join(exp)})"
        print(f"{vname}: and={a} or={o} key={k} -> {state}")


if __name__ == "__main__":
    main()
