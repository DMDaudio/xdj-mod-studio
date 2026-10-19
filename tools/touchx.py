#!/usr/bin/env python3
"""touchx.py [SECONDS] -- READ-ONLY capture to pin the touch X (left/right)
byte(s) in the PANL payload. One shell session; reads the 32-byte payload
head (0x0B6C7A30..+31) in a loop, timestamped, while the user holds known
screen positions. Never writes. Saves raw samples to a timestamped file.

PAY = 0x0B6C7A30. Known: +21 touch-down (01 held / 00 idle), +22/23 Y (BE),
+24/25 pressure (BE). X candidates are in +0..15 (the head). Diff the head
bytes between a far-LEFT hold and a far-RIGHT hold -> the byte(s) that track
X.
"""
import re
import sys
import time

sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain, drain_values

PAY = 0x0B6C7A30


def sample(h):
    q = f"{PAY:x},1fBR\r\x00".encode()
    h.write(q + b"\x00" * (26 - len(q)))
    raw = drain_values(h, 32, 3.0)
    v = [int(x, 16) for x in re.findall(
        r"\b([0-9A-Fa-f]{2})\b(?:\s|$)", raw.decode("ascii", "replace"))]
    return v[-32:] if len(v) >= 32 else None


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    out = f"/tmp/touchx_{int(time.time())}.log"
    rows = []
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        drain(h, 1.5)
        t0 = time.time()
        while time.time() - t0 < secs:
            v = sample(h)
            if not v:
                continue
            t = time.time() - t0
            down, y, p = v[21], (v[22] << 8) | v[23], (v[24] << 8) | v[25]
            head = " ".join(f"{b:02x}" for b in v[:16])
            line = (f"{t:6.1f} down={down} Y={y:#06x} P={p:#06x} | head {head}")
            print(line, flush=True)
            rows.append((t, down, y, p, list(v)))
    finally:
        h.close()
    with open(out, "w") as f:
        for t, down, y, p, v in rows:
            f.write(f"{t:.2f} {down} {y} {p} " + " ".join(map(str, v)) + "\n")
    # quick auto-analysis: group pressed samples, show which head bytes vary
    pressed = [r for r in rows if r[1]]
    print(f"\n{len(rows)} samples, {len(pressed)} with a finger down -> {out}")
    if pressed:
        import statistics
        for b in range(16):
            vals = [r[4][b] for r in pressed]
            spread = max(vals) - min(vals)
            if spread >= 8:
                print(f"  PAY+{b}: varies {min(vals):#04x}..{max(vals):#04x} "
                      f"(spread {spread}) <- X candidate")


if __name__ == "__main__":
    sys.exit(main())
