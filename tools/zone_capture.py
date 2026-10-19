#!/usr/bin/env python3
"""zone_capture.py [SECS] -- log touch X/Y/P payload while user holds buttons.

Read-only, single shell open for the whole session. Samples the payload
coords (~2.5s cycle: two 6-value BRs) and prints timestamped lines; also
saves to /tmp/zone_capture_<ts>.log. HOLD each button 4-5 s.

Usage: .venv/bin/python tools/zone_capture.py 150
"""
import re
import sys
import time

import hid

sys.path.insert(0, "tools")
from xdj700_usb_shell import open_deck, drain, PIONEER_VID

X_WIN = (0x0B6C7A40, 16)   # payload tail: PAY+16..31 (X? Y P area)
YP_WIN = (0x0B6C7A50, 16)  # assembled slots area 0x0B6C7A50..5F


def read6(h, addr):
    q = f"{addr:x},fBR\r\x00".encode()
    h.write(q + b"\x00" * (26 - len(q)))
    raw = drain(h, 4.5).decode("ascii", "replace")
    return re.findall(r"\b([0-9A-Fa-f]{2})\b(?:\s|$)", raw)


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    fn = f"/tmp/zone_capture_{int(time.time())}.log"
    f = open(fn, "w")
    try:
        drain(h, 1.0)
        h.write(b"$CDJ\r\x00" + b"\x00" * 20)
        drain(h, 1.5)
        print("=== GO -- hold each button 4-5s, one at a time ===",
              flush=True)
        f.write("GO\n")
        t0 = time.time()
        while time.time() - t0 < secs:
            t = time.time() - t0
            x6 = read6(h, X_WIN[0])
            y6 = read6(h, YP_WIN[0])
            if len(x6) >= 2 and len(y6) >= 2:
                line = (f"t={t:6.1f}  PAY16_31: {' '.join(x6)}   "
                        f"SLOTS: {' '.join(y6)}")
            else:
                line = f"t={t:6.1f}  (short read x={len(x6)} y={len(y6)})"
            print(line, flush=True)
            f.write(line + "\n")
            f.flush()
    finally:
        h.close()
        f.close()
        print(f"saved {fn}", flush=True)


if __name__ == "__main__":
    main()
