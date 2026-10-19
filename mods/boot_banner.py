#!/usr/bin/env python3
"""mod: boot_banner -- retarget a literal-encoded diagnostic string.

Uses the literal-only path: the bytes we change were emitted as LZSS literals
in the stock stream, so the deck reproduces them exactly no matter how it
resolves matches. Nothing is recompressed and the stream length is unchanged.

Same-length replacement only; the mod refuses anything else.
"""
import json, struct, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
from litpatch import map_literals

TARGET = b'IDLE stack error'
NEW    = b'IDLE stack ERR!!'
BLK, HDR = 0, 0x10000


def apply(work):
    assert len(TARGET) == len(NEW), 'replacement must be the same length'
    img = open(f'{work}/MAIN.img', 'rb').read()
    ln = struct.unpack_from('<I', img, HDR)[0]
    dec, lit = map_literals(img[HDR + 8:HDR + 8 + ln])
    o = dec.find(TARGET)
    if o < 0:
        sys.exit(f'{TARGET!r} not found')
    miss = [o + k for k in range(len(TARGET)) if o + k not in lit]
    if miss:
        sys.exit(f'not literal-encoded at {[hex(x) for x in miss]}')
    edits = {o + k: NEW[k] for k in range(len(NEW)) if NEW[k] != TARGET[k]}
    p = f'{work}/MAIN.blk{BLK:02d}.litpatch.json'
    json.dump({str(k): v for k, v in edits.items()}, open(p, 'w'))
    print(f'  {TARGET.decode()!r} -> {NEW.decode()!r} at {o:#x} '
          f'({len(edits)} bytes, all literal-encoded)')


if __name__ == '__main__':
    apply(sys.argv[1] if len(sys.argv) > 1 else 'work')
