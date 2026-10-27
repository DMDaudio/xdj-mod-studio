#!/usr/bin/env python3
"""pm_probe.py -- READ-ONLY phase meter diagnosis on the deck: is pm_hook
drawing (pm_draws moving), which layer, the gate pixels it compares, the
screen mode, the beat count it snapshotted, and the position word."""
import struct
import sys
import time
sys.path[:0] = [".", "tools"]


def main():
    import hid
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, send
    s = {}
    for ln in open("build/phasemeter/pm.syms"):
        a, _, n = ln.split()
        s[n.lstrip("_")] = int(a, 16)
    r32 = lambda h, a: struct.unpack("<I", bytes(br(h, a, 4)))[0]
    r16 = lambda h, a: struct.unpack("<H", bytes(br(h, a, 2)))[0]
    c = [d for d in hid.enumerate() if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    h = open_deck(PIONEER_VID, c[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        d0 = r32(h, s["pm_draws"]); t0 = time.time()
        time.sleep(2)
        d1 = r32(h, s["pm_draws"]); dt = time.time() - t0
        lay = r32(h, s["pm_layer"])
        print(f"pm_draws {d0} -> {d1} ({(d1 - d0) / dt:.1f}/s), layer {lay:#x}, "
              f"mode word {r32(h, 0x0BA371D4)}, pm_mode {r32(h, s['pm_mode']) & 0xFF}, "
              f"beats {r32(h, s['pm_n'])}, pos150 {r32(h, 0x0C0E6D18)}, flush hook {r32(h, 0x08C92E20):#x}, "
              f"stripped {r32(h, s['pm_stripped']) if 'pm_stripped' in s else '?'}, "
              f"rejects {r32(h, s['pm_rejects']) if 'pm_rejects' in s else '?'}")
        g = lambda n, k=1: [r32(h, s[n] + 4 * i) for i in range(k)] if n in s else ["?"]
        print(f"  gate fails {g('pm_visfail')[0]} last px {g('pm_vispx')[0]:#x}" if 'pm_visfail' in s else "  (old build: no gate diag)")
        if "pm_nmiss" in s:
            print(f"  not stripped (touching the meter): {g('pm_nmiss')[0]}, last "
                  + " ".join(f"{v:08x}" for v in g('pm_miss', 8)))
            print(f"  last stripped: " + " ".join(f"{v:08x}" for v in g('pm_last', 8)))
            print(f"  fills touching the meter (kept): {g('pm_nother')[0]}, last {g('pm_otherop')[0]:08x}"
                  + (f"; fills stripped {g('pm_nfill')[0]}" if 'pm_nfill' in s else "")
                  + (f"; lines stripped {g('pm_nline')[0]}" if 'pm_nline' in s else ""))
        if "pm_nseen" in s:
            n = min(r32(h, s["pm_nseen"]), 8)
            for j in range(n):
                print(f"  seen[{j}]: " + " ".join(f"{v:08x}" for v in g('pm_seen', 64)[8 * j:8 * j + 8]))
        if "pm_nothl" in s:
            n = min(r32(h, s["pm_nothl"]), 16)
            oth = g('pm_other', 128)
            for j in range(n):  # cmd = 5 words + shape
                print(f"  other-layer[{j}]: layer {oth[8*j]:08x} stride {oth[8*j+1]} cmd "
                      + " ".join(f"{v:08x}" for v in oth[8 * j + 2:8 * j + 8]))
        if 0x08000000 <= lay < 0x10000000:
            for y in (124, 150, 116, 120):
                row = [r16(h, lay + 2 * (y * 800 + x)) for x in (254, 258, 260, 366, 471, 473, 476)]
                print(f"  y{y}: " + " ".join(f"{v:04x}" for v in row) + "   (x 254 258 260 366 471 473 476)")
    finally:
        h.close()


if __name__ == "__main__":
    main()
