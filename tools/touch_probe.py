#!/usr/bin/env python3
"""touch_probe.py [SECONDS] -- capture panel-struct snapshots for touch mapping.

READ-ONLY. Deck connected over rear USB-B, powered on, track NOT required.
While it runs, press/hold/drag on the touchscreen (and jog/needle for
reference). It dumps the 128-byte panel struct (keys + payload copy) every
~2s plus the KL keylog at start/end, saving everything to a timestamped
file. Diff snapshots offline to find where touch data lands.

Usage: .venv/bin/python tools/touch_probe.py 60
"""
import sys, time, hid
sys.path.insert(0, "tools")
from xdj700_usb_shell import open_deck, drain, PIONEER_VID

BASE, SIZE = 0x0B6C7A28, 128
CHUNK = 32  # shell returns max 32 values per BR; stitch 4 windows


def cmd(h, payload, secs=2.0):
    h.write(payload + b"\x00" * (26 - len(payload)))
    return drain(h, secs)


def snapshot(h):
    """Same session, LONG drains: 32-value BR replies arrive across many
    slow 16B reports (2s truncates to nothing). First window answers
    fully; later windows may truncate (still parse what's there)."""
    out = []
    for off in range(0, SIZE, CHUNK):
        out.append(cmd(h, f"{BASE + off:x},{CHUNK - 1:x}BR\r\x00".encode(), 9.0))
    return out


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        cmd(h, b"$CDJ\r\x00", 1.5)
        cmd(h, b"?V\r\x00", 1.5)
        fn = f"/tmp/touch_probe_{int(time.time())}.log"
        f = open(fn, "w")
        f.write("t0 keylog:\n" + cmd(h, b"KL\r\x00", 2.0).decode("ascii", "replace") + "\n")
        print("HOLD THE BUTTON NOW - keep holding until it finishes!",
              flush=True)
        t0 = time.time()
        n = 0
        while time.time() - t0 < secs:
            raws = snapshot(h)
            f.write(f"--- snap {n} t={time.time() - t0:.1f}s ---\n")
            for k, raw in enumerate(raws):
                f.write(f"win{k} @{BASE + k * CHUNK:#x} ({len(raw)}B):\n")
                f.write(raw.decode("ascii", "replace") + "\n")
            f.flush()
            n += 1
        f.write("t1 keylog:\n" + cmd(h, b"KL\r\x00", 2.0).decode("ascii", "replace") + "\n")
        f.close()
        print("DONE - you can let go.", flush=True)
        print(f"saved {n} snapshots to {fn} - deck untouched (reads only)")
    finally:
        h.close()


if __name__ == "__main__":
    main()
