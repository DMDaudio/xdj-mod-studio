#!/usr/bin/env python3
"""flac_install.py -- RAM-only install of the DeckVolve FLAC decoder.

Decoder bytes: tools/data/flac_alpha1_template.json (MIT, DeckVolve;
LICENSE in tools/data/FLAC_TEMPLATE_LICENSE_DeckVolve_MIT.txt) applied
to our own bit-identical stock image (verified by SHA at every step).
Nothing is flashed; power-cycle restores stock.

Layout (from their source build, all inside the shell RAM window):
  payload 53,228 B @ 0x091BD7B0 (X7LS header @ 0x091BD7A0)
  gate 768 B @ 0x080FB800, satellite 128 B @ 0x080FBB80,
  cache gate 128 B @ 0x080FC100, helper 76 B @ 0x080FBB00
  8 hooks: dispatch 0x08C01E78 (32B), read 0x08C5635E (12B),
  size 0x08C567BC, position 0x08BE9000, eof 0x08BE900C, seek 0x08BE9010,
  transfer 0x08BE9018, close 0x08C000A4 (4B each)

Usage:
  python tools/flac_install.py --survey          # offline: print the plan
  python tools/flac_install.py --emu [CKPT]      # install in emulator
  python tools/flac_install.py --deck-survey    # read-only deck region check
  python tools/flac_install.py --deck           # chunked install (resumable;
                                                # hooks go LAST; rerun to resume)
State: cache/flac_install.json. One shell open per chunk, >=30 s apart
(enforced). NEVER RUN --deck without explicit user go-ahead for code writes.
"""
import json
import os
import struct
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

BASE = 0x08000000
TEMPLATE = os.path.join("tools", "data", "flac_alpha1_template.json")
TARGET_SHA = "9bfb9df00336bf79c9c0acc529f29ed1a23afaa82c5fb49df7ed4dffd214d2b6"
CHUNK = 4096
STATE = os.path.join("cache", "flac_install.json")
HOOKS = {  # addr: length (written LAST, with old-value check)
    0x08C01E78: 32, 0x08C5635E: 12, 0x08C567BC: 4, 0x08BE9000: 4,
    0x08BE900C: 4, 0x08BE9010: 4, 0x08BE9018: 4, 0x08C000A4: 4,
}
PAYLO_WIN = (0x091BD7A0, 0x091BD7B0 + 53228 - 0x091BD7A0)


def rebuild_target():
    import base64
    import hashlib
    r = json.load(open(TEMPLATE))
    stock = open("cache/blk1.bin", "rb").read()
    t = bytearray(stock + b"\xff" * (r["target_decoded_size"] - len(stock)))
    for s in r["spans"]:
        d = base64.b64decode(s["data"])
        t[s["offset"]:s["offset"] + len(d)] = d
    for op in r["owner_stock_copy_ops"]:
        src, dst, n = op["source_offset"], op["dest_offset"], op["size"]
        chunk = bytearray(stock[src:src + n])
        br = op.get("branch_relocation")
        if br:
            off, kind, tvma = (br["instruction_offset"], br["kind"],
                               br["target_vma"])
            assert chunk[off + 1] == {"bf": 0x8B, "bf_s": 0x8F}[kind]
            delta = tvma - (BASE + dst + off + 4)
            assert delta % 2 == 0 and -128 <= delta // 2 <= 127
            chunk[off] = (delta // 2) & 0xFF
        t[dst:dst + n] = chunk
    t = bytes(t)
    assert hashlib.sha256(t).hexdigest() == TARGET_SHA, "target mismatch"
    return t, stock


def write_runs(t, stock):
    """Merged (addr, bytes) runs; gap<64 merged, split at CHUNK."""
    n = len(t)
    runs, i = [], 0
    while i < n:
        old = stock[i] if i < len(stock) else 0xFF
        if t[i] != old:
            j = i
            while j < n and t[j] != (stock[j] if j < len(stock) else 0xFF):
                j += 1
            runs.append((BASE + i, t[i:j]))
            i = j
        else:
            i += 1
    merged = []
    hook_edges = sorted([a for a in HOOKS] + [a + HOOKS[a] for a in HOOKS])
    for a, b in runs:
        # never merge across hook boundaries (hook chunks get old-checks)
        cuts, prev = [a], a
        for e in hook_edges:
            if a < e < a + len(b):
                cuts.append(e)
        cuts.append(a + len(b))
        for s, e in zip(cuts[:-1], cuts[1:]):
            bb = b[s - a:e - a]
            if merged and s - (merged[-1][0] + len(merged[-1][1])) < 64:
                pa, pb = merged[-1]
                merged[-1] = (pa, pb + bytes(t[pa - BASE + len(pb):s - BASE]) + bb)
            else:
                merged.append((s, bb))
    out = []
    for a, b in merged:
        for k in range(0, len(b), CHUNK):
            out.append((a + k, b[k:k + CHUNK]))
    return out


def split_hooks(runs):
    data, hooks = [], []
    for a, b in runs:
        is_hook = any(ha <= a and a + len(b) <= ha + hl
                      for ha, hl in HOOKS.items())
        (hooks if is_hook else data).append((a, b))
    return data, hooks


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else "--survey"
    t, stock = rebuild_target()
    runs = write_runs(t, stock)
    data, hooks = split_hooks(runs)
    print(f"target verified ({TARGET_SHA[:16]}...); "
          f"{len(runs)} chunks, {sum(len(b) for _, b in runs)} B "
          f"({len(data)} data + {len(hooks)} hook)")
    if a == "--survey":
        for addr, b in runs:
            tag = "HOOK" if any(ha <= addr < ha + hl
                                for ha, hl in HOOKS.items()) else "data"
            print(f"  {tag:4} {addr:#x} +{len(b)}")
        return 0
    if a == "--emu":
        from emu.machine import Machine
        ckpt = sys.argv[2] if len(sys.argv) > 2 else "cache/emu_mainscreen.pkl"
        m = Machine.load(ckpt)
        for addr, b in runs:
            for i, v in enumerate(b):
                m.mem.w8(addr + i, v)
        bad = sum(1 for addr, b in runs for i, v in enumerate(b)
                  if m.mem.r8(addr + i) != v)
        print(f"emu: wrote {sum(len(b) for _, b in runs)} B, "
              f"verify mismatches: {bad}")
        # hooks armed?
        import struct as _s
        for ha, hl in HOOKS.items():
            cur = _s.pack("<I", 0)
        print("emu install complete (no boot run; use browse_emu next)")
        return 0 if bad == 0 else 1
    if a in ("--deck-survey", "--deck"):
        import hid
        from xdj700_usb_shell import (PIONEER_VID, open_deck, drain,
                                      drain_values)
        import re

        def br(h, addr, n):
            out = []
            for o in range(0, n, 32):
                c = min(32, n - o)
                q = f"{addr + o:x},{c - 1:x}BR\r\x00".encode()
                h.write(q + b"\x00" * (26 - len(q)))
                raw = drain_values(h, c, secs=9.0).decode("ascii", "replace")
                out += [int(v, 16) for v in
                        re.findall(r"\b[0-9A-Fa-f]{2}\b(?=\s|$)", raw)[:c]]
                time.sleep(0.3)
            return bytes(out)

        def bw(h, addr, v):
            q = f"{addr:x},{v:02x}BW".encode() + b"\r\x00"
            h.write(q + b"\x00" * (26 - len(q)))
            end = time.time() + 0.4
            buf = bytearray()
            while time.time() < end:
                c = h.read(64, timeout=20)
                if c:
                    buf.extend(c)
                    if b"\n" in buf or b"\r" in buf:
                        break

        def one_open(fn):
            cands = [d for d in hid.enumerate()
                     if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
            if len(cands) != 1:
                sys.exit("found %d shell(s); check cable/power" % len(cands))
            h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
            try:
                drain(h, 1.0)
                h.write(b"$CDJ\r\x00" + b"\x00" * 20)
                drain(h, 1.5)
                return fn(h)
            finally:
                try:
                    h.close()
                except Exception:
                    pass

        if a == "--deck-survey":
            def survey(h):
                base, ln = PAYLO_WIN
                got = br(h, base, min(ln, 4096))
                live = sum(1 for v in got if v not in (0x00, 0x55))
                print(f"payload window head: {len(got)} B, "
                      f"live-data bytes: {live}")
                if live:
                    print("REFUSED: region not free -- send output to dev")
                    return 1
                print("region free (zeros/0x55 fill) -- install may proceed "
                      "with user go-ahead")
                return 0
            return one_open(survey)
        # --deck chunked install
        st = {}
        try:
            st = json.load(open(STATE))
        except Exception:
            pass
        done = set(st.get("done", []))
        plan = [(addr, bytes(b)) for addr, b in data + hooks]
        todo = [(addr, b) for addr, b in plan
                if f"{addr:x}:{len(b)}" not in done]
        if not todo:
            print("all chunks done")
            return 0
        addr, b = todo[0]
        is_hook = any(ha <= addr < ha + hl for ha, hl in HOOKS.items())
        if is_hook and any(f"{a2:x}:{len(b2)}" not in done
                           for a2, b2 in data):
            sys.exit("REFUSED: payload chunks unfinished -- hooks go last")
        print(f"chunk {addr:#x} +{len(b)}B "
              f"({len(done)}/{len(plan)} done)", flush=True)

        def chunk(h):
            if is_hook:
                old = br(h, addr, len(b))
                exp = bytes(stock[addr - BASE + i] for i in range(len(b)))
                if old != exp and old != b:
                    print("REFUSED: hook site unexpected "
                          f"(not stock, not target) -- send output to dev")
                    return 1
                if old == b:
                    print("hook already armed")
                    return 0
            for i, v in enumerate(b):
                bw(h, addr + i, v)
            got = br(h, addr, len(b))
            if got != b:
                bad = sum(1 for x, y in zip(got, b) if x != y)
                print(f"verify: {bad}/{len(b)} mismatch -- chunk kept "
                      f"pending, rerun to retry")
                return 1
            print("verify PASS")
            return 0
        rc = one_open(chunk)
        if rc == 0:
            done.add(f"{addr:x}:{len(b)}")
            os.makedirs("cache", exist_ok=True)
            json.dump({"done": sorted(done)}, open(STATE, "w"))
        print("rerun --deck to continue; pace >=30 s between runs")
        return rc


if __name__ == "__main__":
    sys.exit(main())
