#!/usr/bin/env python3
"""Rebuild an XDJ-700 .UPD from a work dir. Byte-identical if nothing changed.

If an edited block recompresses larger than the original, the S-record layout
for that region is extended -- but never past the next block's header (or the
end of the image), so one block can never overwrite another.
"""
import json, hashlib, os, struct, subprocess, sys, tempfile
from xdj700 import srec_emit, srec_parse, flatten, find_blocks, MAGIC, crc16_xmodem
import litpatch

LZSS = os.environ.get('XDJ_LZSS',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lzss'))
ORIG = os.environ.get('XDJ_STOCK',
        os.path.expanduser('~/Downloads/cdj3k-mods-0.1.0/XDJ700.UPD'))


def lz(mode, data):
    with tempfile.TemporaryDirectory() as td:
        open(f'{td}/i', 'wb').write(data)
        subprocess.run([LZSS, mode, f'{td}/i', f'{td}/o'], check=True)
        return open(f'{td}/o', 'rb').read()


BOOT_END = 0xC40      # recovery-mode code; never write here
PROP_LIMIT = int(os.environ.get('PROP_LIMIT', 32))   # max match-copy propagation
# 2026-09-05: the default of 32 dates from when propagation was believed to be
# the brick mechanism. BOOTSTUB.md proved it was the stale block checksum
# instead, so this limit now guards a risk that has never actually been
# observed. It is kept because it is still a real risk in principle -- a match
# can copy an edited byte into a non-colour context -- but it is now
# overridable per-build via the environment, deliberately and visibly, rather
# than by editing this file. verify() still enforces the invariant that every
# changed byte performs an intended old->new substitution.

# Region that BRICKS THE DECK when edited. Established the hard way on
# 2026-09-05: two separate builds touching block1 0xCC2A70-0xCC54E0 both
# produced MAIN CPU ERROR (222 direct/1099 propagated, and 98 direct/17
# propagated). A 3-byte edit to the version string at 0x568 -- same compressed
# block -- flashed cleanly twice, so this is region-specific, not a payload
# checksum. What the region actually is remains unknown; the "colour palette"
# reading is unproven. Until someone bisects it on a deck they can afford to
# lose, builds touching it are refused.
# 2026-09-05: QUARANTINE LIFTED. This range was quarantined after two MAIN CPU
# ERRORs that were blamed on "editing that region". BOOTSTUB.md then found the
# real cause -- a stale trailing 16-bit block checksum that repack never
# recomputed -- and probe_green_v2.05 subsequently edited 0xCC5414, which is
# INSIDE this range, and booted fine (UTILITY 2.05). The region was never
# implicated; every payload edit failed because every payload edit shipped a
# bad checksum. Set to an empty range so the guard is inert but the history
# stays readable. See PALETTE.md and PAYLOAD_EDITS.md.
FORBIDDEN = (1, 0)

_boot = None


def stock_boot():
    """First BOOT_END bytes of the stock MAIN image."""
    global _boot
    if _boot is None:
        raw = open(ORIG, 'rb').read()
        e1 = raw.index(b'\r\n') + 2
        e2 = raw.index(b'\r\n', e1) + 2
        L1 = int(raw[:e1 - 2])
        main = raw[e2:e2 + L1].decode('latin1')
        _boot = flatten(srec_parse(main)[0])[0][:BOOT_END]
    return _boot


def build(work, out, verbose=True):
    man = json.load(open(f'{work}/manifest.json'))
    sections = []
    for s in man['sections']:
        name, base = s['name'], s['base']
        img = bytearray(open(f'{work}/{name}.img', 'rb').read())
        layout = [tuple(x) for x in s['layout']]
        blocks = find_blocks(bytes(img))

        litpatched = set()
        # literal-only patches: applied straight to the compressed stream,
        # no recompression, stream length unchanged. Safe even if our match
        # decoding is not byte-exact against the deck's.
        for i, (hdr, ln) in enumerate(blocks):
            lp = f'{work}/{name}.blk{i:02d}.litpatch.json'
            if not os.path.exists(lp):
                continue
            edits = {int(k): v for k, v in json.load(open(lp)).items()}
            allow = set()
            ap = f'{work}/ALLOW_FORBIDDEN'
            if os.path.exists(ap):
                allow = {int(x) for x in open(ap).read().split()}
            bad = [o for o in edits
                   if FORBIDDEN[0] <= o <= FORBIDDEN[1] and o not in allow]
            waived = [o for o in edits
                      if FORBIDDEN[0] <= o <= FORBIDDEN[1] and o in allow]
            if waived and verbose:
                print(f'  {name} blk{i}: *** {len(waived)} edit(s) in the '
                      f'quarantined region waived by {work}/ALLOW_FORBIDDEN: '
                      + ', '.join(hex(o) for o in sorted(waived)) + ' ***')
            if bad:
                sys.exit(
                    f'{name} blk{i}: REFUSING TO BUILD -- {len(bad)} edit(s) fall in '
                    f'{FORBIDDEN[0]:#x}-{FORBIDDEN[1]:#x}.\n'
                    f'  Editing that region bricked a real XDJ-700 TWICE on '
                    f'2026-09-05 (MAIN CPU ERROR both times).\n'
                    f'  First at {min(bad):#x}. What the region is remains unknown.\n'
                    f'  See build/UNSAFE/DO_NOT_FLASH.txt. Do not lift this without\n'
                    f'  a deck you can afford to lose.')
            src = bytes(img[hdr + 8:hdr + 8 + ln])
            new, _ = litpatch.patch(src, edits)
            assert len(new) == len(src)
            good, msg = litpatch.verify(src, new, edits)
            if not good:
                sys.exit(f'{name} blk{i}: literal patch verify failed: {msg}')
            # HARD CAP ON PROPAGATION -- learned the hard way, 2026-09-05.
            # Direct literal edits are safe under ANY match semantics. Bytes that
            # change because a later match COPIES from them are NOT: where those
            # copies land depends on the match semantics, which are unproven
            # (CODEC.md). A build with 222 direct / 1099 propagated bytes bricked
            # a real deck; one with 3 direct / 0 propagated flashed perfectly.
            direct = len(edits)
            total = int(msg.split()[0])
            prop = total - direct
            if prop > PROP_LIMIT:
                sys.exit(
                    f'{name} blk{i}: REFUSING TO BUILD -- {prop} propagated bytes '
                    f'(limit {PROP_LIMIT}).\n'
                    f'  {direct} direct edits are safe; the {prop} match-copies are not,\n'
                    f'  because our match semantics are unproven. This is what bricked\n'
                    f'  a deck on 2026-09-05. Reduce the edit, or raise PROP_LIMIT\n'
                    f'  deliberately if you accept the risk.')
            img[hdr + 8:hdr + 8 + ln] = new
            litpatched.add(i)
            if verbose:
                print(f'  {name} blk{i}: literal patch -- {msg}, '
                      f'stream length unchanged ({ln})')

        for i, (hdr, ln) in enumerate(blocks):
            if i in litpatched:
                continue          # already edited in place; must not recompress
            p = f'{work}/{name}.blk{i:02d}.bin'
            if not os.path.exists(p):
                continue
            dec = open(p, 'rb').read()
            if dec == lz('d', bytes(img[hdr + 8:hdr + 8 + ln])):
                continue                                        # untouched
            comp = lz('c', dec)
            limit = blocks[i + 1][0] if i + 1 < len(blocks) else len(img)
            avail = limit - (hdr + 8)
            if len(comp) > avail:
                sys.exit(f'{name} blk{i}: {len(comp)} bytes > {avail} available '
                         f'(region {hdr+8:#x}..{limit:#x})')
            img[hdr:hdr + 4] = struct.pack('<I', len(comp))
            img[hdr + 4:hdr + 8] = MAGIC
            img[hdr + 8:hdr + 8 + len(comp)] = comp
            layout = extend(layout, base, hdr, hdr + 8 + len(comp), limit)
            if verbose:
                print(f'  {name} blk{i}: {len(dec)} raw -> {len(comp)} packed '
                      f'(was {ln}), {avail - len(comp)} bytes spare')

        # TRAILING 16-BIT BLOCK CHECKSUM -- the thing that bricked three flashes.
        #
        # The boot stub (MAIN.img 0x0AF6-0x0B7E, uncompressed and therefore
        # trustworthy) does this, per block:
        #
        #     len = *(u32 *)base;
        #     s   = 0; for (p = base; p < base + 4 + len; p++) s += *p;
        #     if ((uint16_t)s != *(uint16_t *)(base + 4 + len)) -> error path
        #
        # A plain unsigned byte sum truncated to 16 bits, stored little-endian
        # immediately after the summed region. Verified against stock: block0
        # 0xc948 @0x2f324, block1 0xd54a @0x6ea79f, both exact.
        #
        # repack never recomputed it. Every payload edit therefore shipped a
        # stale sum and took the error branch on boot -- which is why 3 bytes
        # bricked the deck exactly as hard as 222. See BOOTSTUB.md.
        for i, (hdr, ln) in enumerate(find_blocks(bytes(img))):
            end = hdr + 4 + ln
            if end + 2 > len(img):
                sys.exit(f'{name} blk{i}: no room for the block checksum at '
                         f'{end:#x} (image ends {len(img):#x})')
            old = struct.unpack_from('<H', img, end)[0]
            got = sum(img[hdr:end]) & 0xFFFF
            if got != old:
                struct.pack_into('<H', img, end, got)
                if verbose:
                    print(f'  {name} blk{i}: block checksum {old:#06x} -> '
                          f'{got:#06x}  (summed {hdr:#x}..{end:#x})')

        # hard guard: the boot stub implements recovery mode. If it is gone,
        # a bad flash is unrecoverable, so refuse to build rather than warn.
        if name == 'MAIN':
            if bytes(img[:BOOT_END]) != stock_boot():
                sys.exit('REFUSING TO BUILD: boot stub 0x0-%#x was modified. '
                         'That code implements recovery mode (power on holding '
                         'IN + RELOOP/EXIT); changing it risks an '
                         'unrecoverable brick.' % BOOT_END)

        lines = [s['preamble']]
        for addr, ln in layout:
            o = addr - base
            lines.append(srec_emit(addr, bytes(img[o:o + ln])))
        # postamble carries the terminator record plus a 2-byte CRC trailer
        tail = s['postamble'][:-2] if len(s['postamble']) >= 2 else s['postamble']
        body = ('\r\n'.join(lines) + '\r\n' + tail).encode('latin1')
        crc = crc16_xmodem(body)
        body += struct.pack('<H', crc)
        old = s['postamble'][-2:].encode('latin1')
        if verbose and body[-2:] != old:
            print(f'  {name}: CRC-16/XMODEM {struct.unpack("<H", old)[0]:#06x} '
                  f'-> {crc:#06x}')
        sections.append(body)

    hdr = b''.join(b'%d\r\n' % len(x) for x in sections)
    open(out, 'wb').write(hdr + b''.join(sections))
    return out


def extend(layout, base, blk_start, need_end, limit):
    """Grow the contiguous record run that holds blk_start so it covers
    need_end. Never crosses into the next run, and never past `limit`."""
    a0 = base + blk_start
    runs, cur = [], [layout[0]]
    for rec in layout[1:]:
        if rec[0] == cur[-1][0] + cur[-1][1]:
            cur.append(rec)
        else:
            runs.append(cur); cur = [rec]
    runs.append(cur)
    for ri, run in enumerate(runs):
        if run[0][0] <= a0 < run[-1][0] + run[-1][1]:
            covered = run[-1][0] + run[-1][1]
            want = base + need_end
            if covered >= want:
                return layout
            hard = base + limit
            nxt = runs[ri + 1][0][0] if ri + 1 < len(runs) else hard
            hard = min(hard, nxt)
            if want > hard:
                sys.exit(f'block at {blk_start:#x} needs {want:#x}, '
                         f'run capped at {hard:#x}')
            stride = run[0][1]
            extra = [(a, stride) for a in range(covered, want, stride)]
            print(f'    extended records {covered:#x}..{extra[-1][0]+stride:#x} '
                  f'({len(extra)} new)')
            return sorted(layout + extra)
    sys.exit(f'no record run contains {a0:#x}')


if __name__ == '__main__':
    work = sys.argv[1] if len(sys.argv) > 1 else '../work'
    out = sys.argv[2] if len(sys.argv) > 2 else '/tmp/REPACK.UPD'
    build(work, out)
    o, n = open(ORIG, 'rb').read(), open(out, 'rb').read()
    print(f'orig {len(o)}  {hashlib.sha256(o).hexdigest()[:16]}')
    print(f'new  {len(n)}  {hashlib.sha256(n).hexdigest()[:16]}')
    print('BYTE-IDENTICAL' if o == n else 'differs (expected if you edited a block)')
