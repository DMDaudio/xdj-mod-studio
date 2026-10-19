#!/usr/bin/env python3
"""detail_fake.py -- make the emulated deck draw its CENTRE waveform.

The emu has no audio/DSP, so the detail painter FUN_08DC39B6 runs with an
empty buffer. This fills the painter's inputs by hand from the track's
real ANLZ data: buffer of {height, whiteness} entries (decoded PWV3,
150/s) at a free RAM spot, its pointer at [0x0BA3C050+0x2EE18], length
at [0x0BA3C050], play position at [0x0C0E6B7C] (+4 = end).
Used to develop/validate the centre-waveform RGB hook in the emulator.
"""
import os
import struct
import sys

sys.path[:0] = [".", "tools"]
from rgb_overview import section_from_file

DSTRUCT, BUFPTR_OFF, POS = 0x0BA3C050, 0x2EE18, 0x0C0E6B7C
BUF = 0x0F000000


def pwv3_entries(ext):
    p3 = section_from_file(ext, b"PWV3")
    out = bytearray()
    for b in p3:
        out += bytes([b & 0x1F, b >> 5])     # height, whiteness
    return out


def install(m, ext, pos):
    mem = m.mem
    ent = pwv3_entries(ext)
    for i in range(0, len(ent), 4096):        # must be free RAM
        assert all(mem.r8(BUF + i + k) == 0 for k in range(0, 64, 4))
    for i, b in enumerate(ent):
        mem.w8(BUF + i, b)
    n = len(ent) // 2
    mem.w32(DSTRUCT, n)
    mem.w32(DSTRUCT + BUFPTR_OFF, BUF)
    mem.w32(POS, pos)
    mem.w32(POS + 4, n)
    return n
