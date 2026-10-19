#!/usr/bin/env python3
"""XDJ-700 .UPD unpack / repack.

    updtool.py unpack FW.UPD outdir/
    updtool.py repack  outdir/ NEW.UPD

unpack writes, per section: <name>.img (flat image), <name>.json (metadata),
and <name>.blkNN.bin (each LZSS block, decompressed).
repack recompresses any block whose .bin changed, rebuilds the image,
re-emits S-records and fixes the two length fields in the file header.
"""
import json, os, re, struct, subprocess, sys, hashlib
from xdj700 import srec_parse, srec_emit, flatten, find_blocks, MAGIC

LZSS = os.environ.get('XDJ_LZSS',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lzss'))


def sh(*a):
    subprocess.run(a, check=True)


def split_sections(raw):
    """-> header_len, [(name, text_bytes)]"""
    e1 = raw.index(b'\r\n') + 2
    e2 = raw.index(b'\r\n', e1) + 2
    lens = [int(raw[:e1 - 2]), int(raw[e1:e2 - 2])]
    secs, off = [], e2
    for L in lens:
        blob = raw[off:off + L]
        m = re.search(rb'XDJ-700\s+(\w+)Ver', blob)
        secs.append(((m.group(1).decode() if m else 'SEC%d' % len(secs)), blob))
        off += L
    assert off == len(raw), 'trailing bytes'
    return e2, secs


def cmd_unpack(upd, outdir):
    raw = open(upd, 'rb').read()
    os.makedirs(outdir, exist_ok=True)
    _, secs = split_sections(raw)
    manifest = []
    for name, blob in secs:
        text = blob.decode('latin1')
        chunks, start = srec_parse(text)
        img, base = flatten(chunks)
        # everything before the first S-record is the section's literal preamble
        pre = text[:re.search(r'S[0-9][0-9A-F]{2}', text).start()]
        open(f'{outdir}/{name}.img', 'wb').write(img)
        blocks = []
        for i, (hdr, ln) in enumerate(find_blocks(img)):
            comp = img[hdr + 8:hdr + 8 + ln]
            p = f'{outdir}/{name}.blk{i:02d}.bin'
            open(p + '.lz', 'wb').write(comp)
            sh(LZSS, 'd', p + '.lz', p)
            os.remove(p + '.lz')
            dec = open(p, 'rb').read()
            blocks.append(dict(index=i, hdr=hdr, comp_len=ln,
                               dec_len=len(dec),
                               dec_sha=hashlib.sha256(dec).hexdigest()))
            print(f'  {name} block{i}: {ln} -> {len(dec)} bytes @ {hdr:#x}')
        meta = dict(name=name, base=base, start=start, preamble=pre,
                    img_len=len(img), blocks=blocks,
                    chunk_stride=len(chunks[0][1]),
                    sha=hashlib.sha256(img).hexdigest())
        json.dump(meta, open(f'{outdir}/{name}.json', 'w'), indent=1)
        print(f'{name}: image {len(img)} bytes @ {base:#x}, '
              f'{len(chunks)} records, {len(blocks)} blocks')
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != 'unpack':
        sys.exit('usage: updtool.py unpack FW.UPD outdir/   (repack: repack.py)')
    sys.exit(cmd_unpack(sys.argv[2], sys.argv[3]))
