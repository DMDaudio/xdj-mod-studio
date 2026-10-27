#!/usr/bin/env python3
"""clock_probe.py -- READ-ONLY: is the deck's position clock moving?
One shell session, two snapshots ~3 s apart while a track plays:
  POSL [0x0C0E6D1C] (150/s, what the phase meter reads),
  SH_POS [0x09D246E0] (75/s, the word that froze on silicon),
  play mode [0x0B6C4264], ANLZ ctx beat grid (PQTZ tag + beat count).
Nothing is written."""
import struct
import sys
import time
sys.path[:0] = [".", "tools"]

POSL, SHPOS, MODE, CTX_TAB, PQTZ = 0x0C0E6D1C, 0x09D246E0, 0x0B6C4264, 0x09826BE4, 0xD7C


def main():
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send

    def r32(h, a):
        return struct.unpack("<I", bytes(br(h, a, 4)))[0]
    cands = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        snaps = []
        for i in range(2):
            t = time.time()
            snaps.append((t, r32(h, POSL), r32(h, SHPOS), r32(h, MODE)))
            print(f"t={t:.1f} POSL={snaps[-1][1]} SH_POS={snaps[-1][2]} mode={snaps[-1][3]}", flush=True)
            if i == 0:
                time.sleep(3)
        (t0, p0, s0, _), (t1, p1, s1, _) = snaps
        dt = t1 - t0
        print(f"POSL moved {p1 - p0} in {dt:.1f}s ({(p1 - p0) / dt:.0f}/s, want ~150)")
        print(f"SH_POS moved {s1 - s0} ({(s1 - s0) / dt:.0f}/s, 75 if alive)")
        ctx = r32(h, CTX_TAB)
        if 0x08000000 <= ctx < 0x10000000:
            tag, esz, n = r32(h, ctx + PQTZ), r32(h, ctx + PQTZ + 0x10), r32(h, ctx + PQTZ + 0x14)
            print(f"ctx {ctx:#x}: PQTZ tag {tag:#x} (want 0x5a545150) entry size {esz} beats {n}")
        else:
            print(f"ctx pointer {ctx:#x} (no track context)")
    finally:
        h.close()


if __name__ == "__main__":
    main()
