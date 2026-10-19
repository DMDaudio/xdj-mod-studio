#!/usr/bin/env python3
"""mod: version_tag -- stamp a marker into the firmware version table.

Writes into the space padding that already follows each field, so every field
keeps its offset and total length. This is the canary mod: if the deck shows
the tag, your build pipeline reached the hardware.
"""
import sys

BLK = 'MAIN.blk00.bin'


def apply(work, tag=b'-MOD'):
    p = f'{work}/{BLK}'
    d = bytearray(open(p, 'rb').read())
    anchor = d.find(b'XDJ-700')
    if anchor < 0:
        sys.exit('version table not found')
    end = anchor + len(b'XDJ-700')
    if bytes(d[end:end + len(tag)]) != b' ' * len(tag):
        sys.exit(f'no padding at {end:#x}; refusing to shift bytes')
    before = bytes(d[anchor - 4:end + 12])
    d[end:end + len(tag)] = tag
    open(p, 'wb').write(d)
    print(f'  patched {BLK} @ {end:#x}: {before!r}')
    print(f'                     -> {bytes(d[anchor-4:end+12])!r}')
    return anchor, end


if __name__ == '__main__':
    apply(sys.argv[1] if len(sys.argv) > 1 else 'work')
