#!/usr/bin/env python3
"""mem_states.py STATE  (STATE = idle | loaded | playing | report)

READ-ONLY. Finds memory that is free in EVERY deck state, not just at idle
(2026-10-12: 0x0977xxxx read empty at idle, the deck used it once a track
loaded -> blank centre waveform + freeze on play).

Run once per state, with the deck put in that state by the user:
  idle     fresh boot, no stick
  loaded   stick in, a long track loaded (not playing)
  playing  the same track playing for 20+ seconds
Each run samples one 32-byte row every 1 KB over the candidate ranges and
merges what it saw into ~/.xdj700_memstates.json. `report` lists runs of
memory that read zero in every recorded state (largest first); a home for
code still needs a FULL survey of the chosen run before anything is written.
"""
import json
import os
import sys
import time

sys.path[:0] = [".", "tools"]
DB = os.path.expanduser("~/.xdj700_memstates.json")
STEP = 0x1000                      # one row per 4 KB (~1 min per state)
RANGES = [(0x09700000, 0x09A00000),      # around our snapshot area
          (0x091C0000, 0x09300000)]      # just past the firmware image
STATES = ("idle", "loaded", "playing")


def sample(state):
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 1]
    if len(c) != 1:
        sys.exit(f"found {len(c)} shell interface(s)")
    h = open_deck(PIONEER_VID, c[0]["product_id"], 1)
    try:
        db = json.load(open(DB))
    except Exception:
        db = {}
    seen = db.setdefault(state, {})
    t = time.time()
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        for lo, hi in RANGES:
            for a in range(lo, hi, STEP):
                v = bytes(br(h, a, 32))
                if any(v):
                    seen[f"{a:#x}"] = 1
                else:
                    seen.setdefault(f"{a:#x}", 0)
    finally:
        h.close()
    json.dump(db, open(DB, "w"))
    used = sum(seen.values())
    print(f"{state}: {len(seen)} rows sampled in {time.time() - t:.0f}s, "
          f"{used} non-zero")


def report():
    db = json.load(open(DB))
    have = [s for s in STATES if s in db]
    print("states recorded:", have)
    if len(have) < 3:
        print("record all three states before trusting any result")
    rows = sorted({int(k, 16) for s in have for k in db[s]})
    free = [a for a in rows if all(db[s].get(f"{a:#x}", 1) == 0 for s in have)]
    runs, start, prev = [], None, None
    for a in free:
        if start is None or a != prev + STEP:
            if start is not None:
                runs.append((start, prev + STEP))
            start = a
        prev = a
    if start is not None:
        runs.append((start, prev + STEP))
    runs.sort(key=lambda r: r[0] - r[1])
    for lo, hi in runs[:12]:
        print(f"  {lo:#010x}..{hi:#010x}  {(hi - lo) // 1024:5d} KB zero in all recorded states")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "report"
    if arg == "report":
        report()
    elif arg in STATES:
        sample(arg)
    else:
        sys.exit(__doc__)
