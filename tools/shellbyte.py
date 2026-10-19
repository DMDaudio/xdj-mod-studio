#!/usr/bin/env python3
"""shellbyte.py -- tiny shell helpers shared by the apply tools:
cmd() (write a padded payload + drain + print) and read_byte() (single
8-bit RAM read with a parsed reply). Pure helper module, no side effects.
"""
import sys

sys.path.insert(0, "tools")
from xdj700_usb_shell import drain, show          # noqa: F401


def cmd(h, payload, tag, secs=2.0):
    print(f"--> {payload!r}")
    h.write(payload + b"\x00" * (26 - len(payload)))
    raw = drain(h, secs)
    show(tag, raw)
    return raw


def read_byte(h, addr):
    """Single 8-bit read: <addr>,0BR. Returns (hex_string, raw_reply)."""
    raw = cmd(h, f"{addr:x},0BR".encode(), f"read {addr:#x}", 1.5)
    vals = raw.split()
    if not vals:
        return "", raw
    return vals[0], raw
