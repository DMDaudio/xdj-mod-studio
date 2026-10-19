#!/usr/bin/env python3
"""safety.py -- the gate. Nothing gets written or flashed without passing this.

    safety.py STOCK.UPD CANDIDATE.UPD

WHY THIS EXISTS
The XDJ-700 this project was built on was bricked. The mechanism is known
exactly, and it is counterintuitive:

    boot stub 0x0B7C:  cmp/eq r1,r5
        sums EQUAL     -> table[0] -> TRUST the payload and run it
        sums DIFFER    -> table[1] -> MAIN CPU ERROR -> USB recovery works

A WRONG checksum fails SAFE. A CORRECT checksum removes the safety net: the
deck stops checking and executes whatever is there. Every earlier bad build
shipped a stale sum and was recoverable. The build that bricked the deck was
the first one with a correctly recomputed sum -- and its payload was still
generated with the wrong LZSS codec, so the deck faithfully ran garbage.

The lesson is not "get the checksum right". It is: once the checksum is right,
the payload has to be right, because nothing downstream will catch it.

So this file checks the PAYLOAD, byte for byte, against invariants that a
colour-only edit cannot violate. It is deliberately paranoid and it refuses by
default.

WHAT IT CANNOT DO
It cannot prove the deck boots. No software check can. It proves the file is
structurally identical to Pioneer's except for pixels inside verified bitmap
regions. That is a strong claim, and it is still not a hardware test.
"""
import os, re, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', 'mods'))
import lzss_boot as L
import sh4sim
from fwtool import parse, assemble, crc16_xmodem
import themes as TH

BOOTSTUB_END = 0xC40
# How much of each block to run through the SH-4 emulator. Full emulation of
# block1 is ~318M instructions (minutes); the default verifies a prefix on every
# build. XDJ_EMU=full forces the whole thing.
_e = os.environ.get('XDJ_EMU', '2000000')
EMU_BYTES = None if _e == 'full' else int(_e)
WORD = re.compile(rb'[A-Za-z]{4,}')
VOWEL = re.compile(rb'[aeiouAEIOU]')


def periodic(s, maxp=12):
    """True if s is a short repeating pattern -- the signature of pixel data."""
    for p in range(1, min(maxp, len(s) // 3) + 1):
        if s[:len(s) - p] == s[p:]:
            return True
    return False


class Report:
    def __init__(self):
        self.rows = []
        self.fatal = 0

    def add(self, ok, name, detail='', critical=True):
        self.rows.append((ok, name, detail, critical))
        if not ok and critical:
            self.fatal += 1
        return ok

    def show(self):
        for ok, name, detail, crit in self.rows:
            tag = 'PASS' if ok else ('FAIL' if crit else 'warn')
            print(f"  [{tag}] {name}" + (f"  -- {detail}" if detail else ''))
        print()
        if self.fatal:
            print(f"REFUSED: {self.fatal} critical check(s) failed. "
                  f"Do not flash this file.")
        else:
            print("PASSED all structural checks.")
            print("This is NOT a hardware test. It proves the file differs from")
            print("Pioneer's only in pixels inside verified bitmap regions.")
        return self.fatal == 0


def strings_of(b, n=6):
    return set(re.findall(rb'[ -~]{%d,}' % n, b))


def real_text(b, n=8):
    """Strings that are actually text, not artwork that happens to be printable.

    Dithered pixel data produces printable runs like b'f)f)f)e)f)f)f)e)' and
    b'(BHB(BIJ(BHB(BIJ'. Three filters remove them: a space (artwork almost
    never contains 0x20 in a text-like run), a >=4-letter pronounceable word,
    and rejection of short repeating periods.

    This check does NOT use the art-window mask, so it is genuinely independent
    evidence that no real firmware text was harmed -- if the mask were wrong
    and a string table got recoloured, this would catch it.
    """
    out = set()
    for s in re.findall(rb'[ -~]{%d,}' % n, b):
        if b' ' not in s:
            continue                                  # real messages have spaces
        m = WORD.search(s)
        if not m:
            continue                                  # no >=4-letter word
        if not VOWEL.search(m.group()):
            continue                                  # not pronounceable
        if periodic(s):
            continue                                  # repeating pixel pattern
        out.add(s)
    return out


def utf16_strings(b):
    """(start, end) of UTF-16LE runs that are real messages: >= 5 printable
    characters, two lowercase letters in a row, >= 5 distinct characters
    (pixel data like 'AAABAAAB' fails the last two)."""
    out = []
    for m in re.finditer(rb'(?:[\x20-\x7e\n]\x00){5,}', b):
        if m.start() % 2:
            continue
        t = m.group().decode('utf-16-le')
        if re.search(r'[a-z]{2}', t) and len(set(t)) >= 5:
            out.append((m.start(), m.end()))
    return out


def text_dense_spans(b, span=2048, frac=0.40):
    """2 KB spans where >= 40% of the u16 values are printable ASCII: the
    UTF-16 message tables (pixel data sits near 2%)."""
    n = len(b) // span
    w = np.frombuffer(b[:n * span], '<u2').reshape(n, span // 2)
    dense = ((w >= 0x20) & (w < 0x7F)).mean(1) >= frac
    return [(i * span, (i + 1) * span) for i in np.flatnonzero(dense)]


def nonart_bytes(dec, mask):
    a = np.frombuffer(dec, np.uint8)
    return a[~mask].tobytes()


def sh4_stats(b):
    w = np.frombuffer(b[:len(b) // 2 * 2], dtype='<u2')
    P = [(0x4F22, 0x4F26), (0x2FE6, 0x6EF6), (0x2FD6, 0x6DF6),
         (0x2FC6, 0x6CF6), (0x2FB6, 0x6BF6), (0x2F86, 0x68F6)]
    return (sum(int((w == a).sum()) for a, _ in P),
            sum(int((w == b_).sum()) for _, b_ in P))


def check(stock_path, cand_path):
    R = Report()
    print(f"stock     {stock_path}")
    print(f"candidate {cand_path}\n")

    # -- 0. the toolchain reproduces the stock file exactly on this input
    raw_s, sec_s = parse(stock_path)
    R.add(assemble(sec_s) == raw_s, "toolchain round-trips the STOCK file byte-identically",
          "if this fails, nothing below can be trusted")

    raw_c, sec_c = parse(cand_path)

    # -- 1. container shape
    R.add(len(sec_c) == len(sec_s), "same section count")
    ms, mc = sec_s[0], sec_c[0]
    R.add(mc.name == ms.name == 'MAIN', "first section is MAIN")
    R.add(len(mc.img) == len(ms.img), "MAIN image length unchanged",
          f"{len(ms.img):,} vs {len(mc.img):,}")
    R.add(mc.base == ms.base, "MAIN load base unchanged")
    R.add(mc.recs == ms.recs, "S-record layout identical",
          f"{len(ms.recs):,} records")
    R.add(sec_c[1].emit() == sec_s[1].emit(), "PANL section byte-identical")

    # -- 2. section CRCs (recomputed independently of the writer)
    for nm, sec in (('MAIN', mc), ('PANL', sec_c[1])):
        body = sec.emit()[:-2]
        stored = int.from_bytes(sec.emit()[-2:], 'little')
        R.add(crc16_xmodem(body) == stored, f"{nm} section CRC-16/XMODEM correct",
              f"{stored:#06x}")

    # -- 3. the boot stub itself must never be touched
    R.add(bytes(mc.img[:BOOTSTUB_END]) == bytes(ms.img[:BOOTSTUB_END]),
          "boot stub 0x0-0xC40 byte-identical",
          "this is the only code that can recover the deck")

    # -- 4. blocks, exactly as the boot stub reads them
    bs, bc = ms.blocks(), mc.blocks()
    R.add(len(bs) == len(bc), "same block count", f"{len(bs)}")
    R.add([h for h, _ in bs] == [h for h, _ in bc], "blocks at the same offsets")
    if len(bs) != len(bc):
        return R.show()

    for (hs, ls), (hc, lc) in zip(bs, bc):
        img = mc.img
        s = sum(img[hc:hc + 4 + lc]) & 0xFFFF
        stored = int.from_bytes(bytes(img[hc + 4 + lc:hc + 6 + lc]), 'little')
        R.add(s == stored, f"block @{hc:#08x} byte-sum matches stored u16",
              f"{s:#06x} vs {stored:#06x}  (boot stub 0x0B7C gate)")

        end_old = hs + 4 + ls + 2
        end_new = hc + 4 + lc + 2
        R.add(end_new <= max(end_old, len(img)), f"block @{hc:#08x} fits its slot",
              f"{lc:,} vs stock {ls:,} ({lc-ls:+,})")
        if end_new < end_old:
            tail = bytes(img[end_new:end_old])
            R.add(all(x == 0xFF for x in tail),
                  f"block @{hc:#08x} freed tail is 0xFF padding", f"{len(tail)} B")

        # -- 5. THE PAYLOAD. this is the check that matters.
        cand_stream = bytes(mc.img[hc + 4:hc + 4 + lc])
        ds = L.decode(bytes(ms.img[hs + 4:hs + 4 + ls]))
        dc = L.decode(cand_stream)
        R.add(len(ds) == len(dc), f"block @{hc:#08x} decodes to the same length",
              f"{len(ds):,}")
        if len(ds) != len(dc):
            continue

        R.add(L.decode(L.encode(dc)) == dc,
              f"block @{hc:#08x} encoder round-trip is byte-exact",
              "our decoder reproduces what our encoder wrote")

        # The check above only proves our Python codec is self-consistent. This
        # one executes the DECK'S OWN decompressor -- the real SH-4 instructions
        # at MAIN.img 0x0900-0x09C2 -- over the candidate stream, and compares.
        # It removes "we transcribed the codec correctly" from the trust chain,
        # which is precisely the assumption that bricked a deck.
        try:
            emu_n = EMU_BYTES if EMU_BYTES else None
            cut = len(cand_stream) if emu_n is None else min(len(cand_stream),
                                                             max(4096, emu_n // 8))
            got, steps = sh4sim.decompress(bytes(mc.img), cand_stream[:cut])
            k = min(len(got), len(dc)) if cut < len(cand_stream) else len(dc)
            ok = bytes(got[:k]) == bytes(dc[:k])
            R.add(ok, f"block @{hc:#08x} FIRMWARE'S OWN decompressor reproduces this payload",
                  f"{steps:,} SH-4 instructions emulated, {k:,} bytes compared"
                  + ("" if cut >= len(cand_stream) else f" (prefix; set XDJ_EMU=full for all)"))
        except Exception as e:
            R.add(False, f"block @{hc:#08x} firmware decompressor emulation",
                  f"could not run: {e}")

        a = np.frombuffer(ds, np.uint8); b = np.frombuffer(dc, np.uint8)
        diff = np.flatnonzero(a != b)
        if len(diff) == 0:
            R.add(True, f"block @{hc:#08x} payload unchanged")
            continue

        n = len(dc) // 2 * 2
        w = np.frombuffer(ds[:n], dtype='<u2')
        art, nb = TH.art_windows(w)
        artbyte = np.zeros(len(dc), bool)
        artbyte[:nb * TH.WIN * 2] = np.repeat(art, TH.WIN * 2)
        # plus rectangles the firmware itself blits as images (measured in
        # the emulator, mods/sprite_map.json; empty unless this is the
        # block1 the map was measured on). Evidence, not a heuristic: the
        # 2DG engine copies exactly these bytes to the screen as pixels.
        spr = TH.proven_sprites(n // 2)
        artbyte[:n] |= np.repeat(spr, 2)

        outside = diff[~artbyte[diff]]
        R.add(len(outside) == 0,
              f"block @{hc:#08x} every changed byte is inside a verified bitmap window "
              f"or emulator-proven sprite",
              f"{len(diff):,} changed, {len(outside):,} outside"
              + (f" first at {outside[0]:#x}" if len(outside) else ""))

        # every change must be a whole-pixel RGB565 change, nothing structural
        wsr = np.frombuffer(ds[:n], dtype='<u2'); wcr = np.frombuffer(dc[:n], dtype='<u2')
        px = np.flatnonzero(wsr != wcr)
        R.add(len(diff) <= len(px) * 2, f"block @{hc:#08x} changes are u16-aligned pixels",
              f"{len(px):,} pixels")

        # transparency keys must survive exactly
        for key, nm in ((0xF81F, 'magenta 0xF81F'), (0x07E0, 'green 0x07E0')):
            R.add(int((wsr == key).sum()) == int((wcr == key).sum()),
                  f"block @{hc:#08x} {nm} transparency key count unchanged",
                  f"{int((wsr==key).sum()):,}")

        # -- 6. code and text must be untouched.
        # Scoped to NON-ART bytes: inside artwork, pixel data coincidentally
        # matches SH-4 opcodes and printable ASCII, and recolouring changes
        # those coincidences. That is expected and harmless.
        nas, nac = nonart_bytes(ds, artbyte), nonart_bytes(dc, artbyte)
        ps, pp = sh4_stats(nas); cs, cp = sh4_stats(nac)
        R.add((ps, pp) == (cs, cp),
              f"block @{hc:#08x} SH-4 prologue/epilogue counts unchanged outside art",
              f"push {ps:,} pop {pp:,}")
        R.add(strings_of(nas) == strings_of(nac),
              f"block @{hc:#08x} all ASCII outside art identical")

        # -- 7. INDEPENDENT of the art mask: real text must survive everywhere.
        ts, tc = real_text(ds), real_text(dc)
        lost = ts - tc
        R.add(not lost, f"block @{hc:#08x} every real text string survives (mask-independent)",
              f"{len(ts):,} text strings, {len(lost)} lost"
              + (f": {sorted(lost)[0][:40]!r}" if lost else ""))

        # -- 8. INDEPENDENT: the UI messages are UTF-16LE, which check 7
        # (8-bit strings) cannot see. Every theme built before 2026-09-27
        # rewrote 1,388 of them ('U' = 0x0055 reads as a blue pixel); the
        # emulator showed "USB Error" as CJK glyphs. Two views: each real
        # UTF-16 string, and every text-dense 2 KB span (catches the
        # Russian / CJK tables, which have no ASCII words to match).
        u16s, u16c = utf16_strings(ds), utf16_strings(dc)
        broke = [s for s in u16s if dc[s[0]:s[1]] != ds[s[0]:s[1]]]
        R.add(not broke, f"block @{hc:#08x} every UTF-16 UI message survives (mask-independent)",
              f"{len(u16s):,} UTF-16 strings, {len(broke)} changed"
              + (f": {ds[broke[0][0]:broke[0][1]].decode('utf-16-le')[:40]!r}"
                 if broke else ""))
        # spans the firmware is seen blitting as images (0x8B3800-0x8B7000:
        # dark-blue pixel runs that look like 'AAAB' text) are artwork
        spans = [(a, b) for a, b in text_dense_spans(ds)
                 if not spr[a // 2:b // 2].any()]
        hit = [a for a, b in spans if ds[a:b] != dc[a:b]]
        R.add(not hit, f"block @{hc:#08x} all text-dense spans (message tables) identical",
              f"{len(spans):,} spans" + (f", first changed at {hit[0]:#x}" if hit else ""))

    return R.show()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(0 if check(sys.argv[1], sys.argv[2]) else 1)
