#!/usr/bin/env python3
"""mod: probe_one -- the smallest possible colour test. THREE payload bytes.

    python3 mods/probe_one.py work [ENTRY_OFFSET]

WHAT IT DOES (default)
Changes one palette entry, block1 0xCC5414, from RGBA 0x2C2C2CFF (#2C2C2C, a
dark grey) to 0x00FF00FF (pure green). Alpha is untouched, so only three bytes
move: 0xCC5415, 0xCC5416, 0xCC5417.

WHY THIS ENTRY

1. Safety. Three payload edits have ever reached a deck: the version string at
   0x568 flashed cleanly twice; two palette builds (222 and 98 direct edits)
   both caused MAIN CPU ERROR. The property that distinguishes the one that
   worked is measurable -- every byte it touched has LZSS fan-out 1, i.e. no
   later match copies it, under BOTH candidate decoders. All three bytes here
   have that property (tools/safegate.py).

2. Visibility, and this is the part that was nearly got wrong. The first
   version of this probe used 0xCC2B18 (#0868D0) in the waveform ramp. Measured
   against the project's own renders, #0868D0 occupies **0 pixels** of the stock
   screenshot -- a null result would have been uninterpretable. #2C2C2C occupies
   **67,779 px, 8.07% of the screen**, spread across the whole display. It is
   present at exactly that count in all five theme presets, which is the check
   that it is genuinely stock: mods/theme.py leaves greys and near-blacks alone,
   so a chrome colour must survive every preset unchanged.

Coverage of the other gate-passing entries, if a follow-up is wanted:

    0xCC5414  #2C2C2C  67,779 px  8.07%   <- this one
    0xCC541C  #090713  11,972 px  1.43%
    0xCC5418  #050415   9,461 px  1.13%
    0xCC5370  #454545   1,273 px  0.15%
    0xCC2B18  #0868D0       0 px  0.00%   <- the first attempt; do not use

WHAT EACH OUTCOME MEANS
  green over ~8% of screen   the region IS the live palette, the fan-out gate
                             works, and themes scale one measured entry at a time
  boots, nothing green       0xCC2A70-0xCC54E0 is not what paints the UI
  MAIN CPU ERROR             the region is load-bearing for some other reason
                             (checksum, or it is not data) -- also decisive

*** This touches the region quarantined in repack.py. It is deliberate and
    authorised for this specific probe; the guard stays up for everything else.
    Recovery: build/RECOVER_stock_v199.UPD, IN + RELOOP/EXIT, top USB port. ***
"""
import json, os, struct, sys

DEFAULT = 0xCC5414
NEW_RGB = (0x00, 0xFF, 0x00)

# byte order per table, from tools/palette.py -- the tables do NOT share one
ORDER = {0xCC2A70: 'RGBA', 0xCC2AC0: 'RGBA', 0xCC2B14: 'ARGB', 0xCC5288: 'RGBA',
         0xCC52A8: 'ARGB', 0xCC52E0: 'RGBA', 0xCC5368: 'RGBA', 0xCC53F8: 'RGBA',
         0xCC54D8: 'ARGB'}


def order_of(entry):
    base = max(b for b in ORDER if b <= entry)
    return ORDER[base]


def apply(work, entry=DEFAULT):
    dec = open(f'{work}/MAIN.blk01.bin', 'rb').read()
    old = struct.unpack_from('<I', dec, entry)[0]
    order = order_of(entry)
    r, g, b = NEW_RGB
    if order == 'ARGB':
        new = (old & 0xFF000000) | (r << 16) | (g << 8) | b
    else:
        new = (r << 24) | (g << 16) | (b << 8) | (old & 0xFF)

    ob, nb = struct.pack('<I', old), struct.pack('<I', new)
    edits = {entry + i: nb[i] for i in range(4) if ob[i] != nb[i]}
    if not edits:
        sys.exit('nothing to change')

    p = f'{work}/MAIN.blk01.litpatch.json'
    cur = json.load(open(p)) if os.path.exists(p) else {}
    cur.update({str(k): v for k, v in edits.items()})
    json.dump(cur, open(p, 'w'), indent=1)

    print(f'  palette {entry:#x} ({order}): {old:#010x} -> {new:#010x}')
    for o, v in sorted(edits.items()):
        print(f'    byte {o:#x}: {ob[o-entry]:#04x} -> {v:#04x}')
    print(f'  {len(edits)} payload byte(s) total')
    open(f'{work}/ALLOW_FORBIDDEN', 'w').write(
        '\n'.join(str(o) for o in sorted(edits)) + '\n')


if __name__ == '__main__':
    apply(sys.argv[1] if len(sys.argv) > 1 else 'work',
          int(sys.argv[2], 0) if len(sys.argv) > 2 else DEFAULT)
