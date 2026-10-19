"""Firmware loader: real .UPD -> flash image for the bus.

Uses tools/fwtool.py (container) + tools/lzss_boot.py (ground-truth codec
transcribed from the boot stub). Falls back to work/MAIN.img if no .UPD
given. Never modifies the source file.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from .paths import repo_root  # noqa: E402  (frozen app: data lives in the repo)
ROOT = repo_root()
import fwtool
import lzss_boot as L


def load_upd(path):
    raw, secs = fwtool.parse(path)
    main = next(s for s in secs if s.name == "MAIN")
    panl = next((s for s in secs if s.name == "PANL"), None)
    return raw, main, panl


def flash_image(main_sec):
    return bytes(main_sec.img)


def decode_block1(main_sec):
    """Return (hdr, stream_len, decoded_bytes) for the big app block."""
    blocks = main_sec.blocks()
    # the app block is the largest
    hdr, ln = max(blocks, key=lambda t: t[1])
    stream = bytes(main_sec.img[hdr + 4:hdr + 4 + ln])
    return hdr, ln, L.decode(stream)


def find_upd(explicit=None):
    if explicit and os.path.exists(explicit):
        return explicit
    for cand in (
        os.path.join(ROOT, "build", "XDJ700.UPD"),
        os.path.join(ROOT, "XDJ700.UPD"),
        os.path.join(ROOT, "FIRMWARE", "XDJ700_stock_v1.15.UPD"),
        os.path.expanduser("~/Downloads/cdj3k-mods-0.1.0/XDJ700.UPD"),
    ):
        if os.path.exists(cand):
            return cand
    # the user's firmware home (made from their own update file)
    from . import fwhome
    h = fwhome.path("MAIN.img")
    if os.path.exists(h):
        return h
    # last resort: work/MAIN.img (already unpacked)
    w = os.path.join(ROOT, "work", "MAIN.img")
    if os.path.exists(w):
        return w
    return None


def load_flash(explicit=None):
    """Return (bytes flash, info dict). Works with .UPD or raw MAIN.img."""
    p = find_upd(explicit)
    if p is None:
        return None, {"error": "no firmware found (pass --upd XDJ700.UPD)"}
    if p.endswith(".img"):
        return open(p, "rb").read(), {"source": p}
    raw, main, panl = load_upd(p)
    blocks = main.blocks()
    return bytes(main.img), {"source": p, "blocks": blocks,
                             "panl": bytes(panl.img) if panl else None}
