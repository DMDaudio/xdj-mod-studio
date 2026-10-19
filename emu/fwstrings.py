"""Dump-driven UI content: labels, colours, keys -- all read live from the
firmware dumps at startup, never hardcoded as the source of truth.

Labels: trusted literal-encoded strings out of MAIN block1 (a literal byte
decodes to itself under ANY match semantics, so these are exact regardless
of the codec debate). Each label carries its block1 offset as provenance;
anything not found in the dump falls back to plain text and is marked
SPRITE (visible on the deck only as bitmap art: QUANTIZE, PERFORM, SLIP,
REMAIN, MEMORY, HOT CUE -- HANDOVER.txt 2b confirmed they have no text).

Colours: the 94 literal RGBA/ARGB entries from tools/palette.py (gradient
tables at block1 0xCC2A70-0xCC54E0) plus on-deck measurements documented in
HANDOVER.txt (fills sampled off a real photograph, therefore approximate).
"""
import os
import re
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

WANT = [
    "BROWSE", "SEARCH", "TAG LIST", "TAG", "LIST", "INFO", "MENU", "UTILITY",
    "PLAYER", "SYNC", "MASTER", "TEMPO", "TIME", "TRACK", "LINK", "USB",
    "LOOP", "BEAT", "GRID", "ZOOM", "CALL", "CUE", "VINYL", "RELOOP",
    "SD", "DISC", "EJECT", "DELETE", "RETURN",
]

# Measured off a real deck photograph (HANDOVER.txt 2b/2c). Approximate by
# nature (camera gamma, backlight) -- kept separate from dump-exact colours.
MEASURED = {
    "fill_browse": (0x24, 0x35, 0x4F, "photo #24354F"),
    "fill_quantize": (0x89, 0x2C, 0x36, "photo #892C36"),
    "fill_title": (0x22, 0x40, 0x72, "photo #224072"),
    "fill_perform": (0x39, 0x3E, 0x54, "photo #393E54"),
    "quantize_red_src": (0xF8, 0x4A, 0x50, "block1 0xCC3C42 x9, match-encoded"),
}

_cache = {}


def _load():
    if "dec" in _cache:
        return _cache["dec"], _cache["lit"]
    from litpatch import map_literals
    img = open(os.path.join(ROOT, "work", "MAIN.img"), "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    dec, lit = map_literals(img[0x40008:0x40008 + ln])
    _cache["dec"], _cache["lit"] = dec, lit
    return dec, lit


def labels():
    """-> {text: (offset|None, source)}  source is 'dump' or 'SPRITE'."""
    dec, lit = _load()
    out = {}
    for t in WANT:
        b = t.encode()
        off = None
        for m in re.finditer(re.escape(b), dec):
            h = m.start()
            if all(h + k in lit for k in range(len(b))):
                off = h
                break
        out[t] = (off, "dump" if off is not None else "SPRITE")
    return out


def colors():
    """-> dict with real palette entries + measured fills.

    wave: the cool-ramp blues that tightly match the on-deck waveform
    pixels (#50A8F8, #3890E8, table 0xCC2AC0). ramp_cool/ramp_warm: full
    gradient lists straight from the dump.
    """
    import palette as P
    dec, lit = _load()
    ents = list(P.entries(dec, lit))
    cool = [e[1] for e in ents if e[3] in ("RGBA", "ARGB")
            and e[1][2] > e[1][0] + 10 and e[1][2] >= e[1][1]]
    warm = [e[1] for e in ents if e[1] not in cool]
    wave = [c for c in ((0x50, 0xA8, 0xF8), (0x38, 0x90, 0xE8)) if c in cool]
    if not wave:
        wave = sorted(cool, key=lambda c: -(c[2]))[:2]
    return {
        "n_entries": len(ents),
        "wave": wave,
        "cool": cool,
        "warm": warm,
        "keys": (0xF8, 0x1F),  # transparency keys are RGB565 constants
        "measured": {k: v[:3] for k, v in MEASURED.items()},
        "measured_prov": {k: v[3] for k, v in MEASURED.items()},
    }


def report():
    dec, lit = _load()
    lab = labels()
    col = colors()
    lines = [f"block1: {len(dec):,} bytes, {len(lit):,} trusted literals",
             f"palette: {col['n_entries']} dump entries, "
             f"wave={['#%02X%02X%02X' % c for c in col['wave']]}"]
    for t, (off, src) in lab.items():
        lines.append(f"  {t:10s} {src:6s} {'@%#x' % off if off is not None else ''}")
    return "\n".join(lines)
