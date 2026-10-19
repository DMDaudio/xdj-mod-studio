#!/usr/bin/env python3
"""deck_zero_survey.py ADDR:LEN ... -- READ-ONLY: are these RAM ranges all
zero on the real deck? (Cave-site validation; the emulator missed live
data at 0x09608000 on 2026-10-04.) One shell open, BR only.

Survey memory (2026-10-12): a range that has read ALL ZERO on THIS deck
(by USB serial) is remembered in ~/.xdj700_surveys.json. Later surveys
of a remembered range read a SPOT CHECK (every 16th 32-byte window,
incl. first and last) instead of all of it; any nonzero byte there
forgets the range and fails like a full survey would. --full forces a
complete read."""
import json
import os
import sys
import time

sys.path.insert(0, "tools")
import hid
from xdj700_usb_shell import PIONEER_VID, open_deck, drain
from rgb_overview_apply import read_block, send

CACHE = os.path.expanduser(os.environ.get("XDJ_SURVEY_CACHE", "~/.xdj700_surveys.json"))
STRIDE = 16                       # spot check: 1 of every 16 windows


def load():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv
    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    cache = load()
    try:
        serial = str(getattr(h, "serial", "") or "unknown")
        known = cache.setdefault(serial, {})
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        for arg in args:
            a, n = (int(x, 16) for x in arg.split(":"))
            assert 0x08000000 <= a and a + n <= 0x10000000
            key = f"{a:#x}:{n:#x}"
            if key in known and not full and serial != "unknown":
                offs = list(range(0, n, 32 * STRIDE))
                if offs[-1] != (n - 1) // 32 * 32:
                    offs.append((n - 1) // 32 * 32)
                nz = []
                for o in offs:
                    b = read_block(h, a + o, min(32, n - o))
                    nz += [o + i for i, x in enumerate(b) if x]
                if nz:
                    known.pop(key, None)
                    print(f"{a:#010x}+{n:#x}: {len(nz)} nonzero in spot check, "
                          f"first at +{nz[0]:#x} (forgot this range)", flush=True)
                else:
                    print(f"{a:#010x}+{n:#x}: ALL ZERO (spot check of "
                          f"{len(offs)} windows; full survey "
                          f"{known[key]})", flush=True)
                continue
            if full:
                b = read_block(h, a, n)
            else:                       # 1 in 4 128-B blocks + the last one
                import fastload
                b = bytearray(n)
                offs = sorted(set(list(range(0, n, 128 * fastload.FIRST_SAMPLE))
                                  + [max(0, (n - 128) & ~3)]))
                for o in offs:
                    b[o:o + 128] = read_block(h, a + o, min(128, n - o))
            nz = [i for i, x in enumerate(b) if x]
            if not nz:
                known[key] = time.strftime("%Y-%m-%d")
            print(f"{a:#010x}+{n:#x}: " + ("ALL ZERO" if not nz else
                  f"{len(nz)} nonzero, first at +{nz[0]:#x}: {b[nz[0]:nz[0]+8].hex(' ')}"),
                  flush=True)
    finally:
        h.close()
        json.dump(cache, open(CACHE, "w"), indent=1)


if __name__ == "__main__":
    main()
