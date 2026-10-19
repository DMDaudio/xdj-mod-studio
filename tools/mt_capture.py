#!/usr/bin/env python3
"""mt_capture.py -- READ-ONLY Master Tempo / DSP-shadow snapshot (one shell open).

Captures the host-side words the DSP key-shift design needs, without
writing anything, flashing anything, or touching the DSP. All addresses
are inside the safe RAM window; BR reads only.

Windows: MT state 0x0B6C4308, cleared word 0x0B6C4304, tempo-dirty
0x0B6C2C48, Q20 ratio 0x0B6C2C50, rate 0x0B6C3E18, derived flag 0x0B6C3C14,
DSP ctx block 0x09D24DC4 area, MT persist bit 0x0A4FD0E8.

Usage (deck powered, USB connected, quit music/MIDI apps first):
  .venv/bin/python tools/mt_capture.py idle
  ... play a track ...
  .venv/bin/python tools/mt_capture.py playing
  ... press MASTER TEMPO ...
  .venv/bin/python tools/mt_capture.py mt-on
  ... move tempo fader ...
  .venv/bin/python tools/mt_capture.py mt-on-moved
  ... press MASTER TEMPO again ...
  .venv/bin/python tools/mt_capture.py mt-off

Wait >=30 s between runs. Each run is ONE shell open. Never chain runs
in a single command. If the deck stops answering, wait minutes, replug.
"""
import re
import sys
import time

import hid

sys.path.insert(0, "tools")
from xdj700_usb_shell import PIONEER_VID, open_deck, drain

# (label, addr, count) -- count = bytes, 1..32 (BR count masked to 5 bits)
WINDOWS = [
    ("mt area 0x0B6C4300 (4304 clr, 4308 MT state)", 0x0B6C4300, 16),
    ("tempo area 0x0B6C2C40 (2C48 dirty, 2C50 Q20 ratio)", 0x0B6C2C40, 32),
    ("rate area 0x0B6C3E10 (3E18 rate)", 0x0B6C3E10, 32),
    ("derived MT 0x0B6C3C10 (3C14 active flag)", 0x0B6C3C10, 16),
    ("dsp ctx 0x09D24DC0 (A84 rev, A85 MT, A88 ratio)", 0x09D24DC0, 32),
    ("persist 0x0A4FD0E0 (0E8 bit6 = MT saved)", 0x0A4FD0E0, 16),
]


def parse_br_reply(raw: bytes):
    txt = raw.decode("ascii", "replace")
    return [v for v in re.findall(r"\b[0-9A-Fa-f]{2}\b(?=\s|$)", txt)]


def main():
    state = sys.argv[1] if len(sys.argv) > 1 else "unlabeled"
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s); check cable/power")
    print(f"--- mt_capture [{state}] {time.strftime('%H:%M:%S')} ---",
          flush=True)
    fn = f"/tmp/mt_capture_{state}_{int(time.time())}.log"
    log = open(fn, "w")
    print(f"raw log: {fn}", flush=True)
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
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
            raw = drain(h, 9.0 if count > 8 else 3.0)
            log.write(f"--- {label} @{addr:08x} ---\n"
                      + raw.decode("ascii", "replace") + "\n")
            log.flush()
            vals = parse_br_reply(raw)
            hexs = " ".join(vals)
            print(f"{label} @ {addr:08x} ({count}B): {hexs}"
                  + ("" if len(vals) >= count else
                     f"   [short: got {len(vals)}/{count}]"), flush=True)
            time.sleep(0.3)
    finally:
        try:
            h.close()
        except Exception:
            pass
        log.close()


if __name__ == "__main__":
    main()
