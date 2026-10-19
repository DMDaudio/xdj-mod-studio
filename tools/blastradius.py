#!/usr/bin/env python3
"""blastradius.py -- how big is a literal patch's real blast radius?

litpatch.py's safety argument is: a LITERAL byte decodes to itself under any
match semantics, so patching it in the compressed stream is codec-independent.
That is true *for that byte*. It is NOT true for everything downstream: the
patched byte also enters the LZSS ring, and every later MATCH that copies from
the ring copies the new value. Which later bytes those are depends entirely on
the match semantics -- which CODEC.md proves we do not have.

So tools/propcost.py's propagation figures are computed with a codec we know is
wrong, and cannot bound the real blast radius. This measures the disagreement
directly: apply the same stream edits, then decode under two different match
semantics and compare how much output each says changed.

    blastradius.py            run the standard comparison
"""
import os, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, '..', 'work')
DEC = os.path.join(HERE, 'decode_prov')
N, F, THR, FILL = 4096, 18, 2, 0x20


def map_literals_v1(src):
    rb = bytearray([FILL] * (N - F) + [0] * F)
    r, out, p, n = N - F, bytearray(), 0, len(src)
    lit = {}
    while p < n:
        fl = src[p]; p += 1
        for b in range(8):
            if p >= n:
                return bytes(out), lit
            if fl >> b & 1:
                lit[len(out)] = p
                c = src[p]; p += 1
                out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
            else:
                if p + 1 >= n:
                    return bytes(out), lit
                b1, b2 = src[p], src[p + 1]; p += 2
                i = b2 | ((b1 & 0xF0) << 4); j = (b1 & 0x0F) + THR + 1
                for k in range(j):
                    c = rb[(i + k) & (N - 1)]
                    out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
    return bytes(out), lit


def decode(stream, variant, tmp):
    sp = os.path.join(tmp, 's.lz')
    op = os.path.join(tmp, f'o.{variant}')
    open(sp, 'wb').write(stream)
    subprocess.run([DEC, sp, op, os.path.join(tmp, 'p.bin'), variant],
                   check=True, stderr=subprocess.DEVNULL)
    return open(op, 'rb').read()


def ndiff(a, b):
    n = min(len(a), len(b))
    d = sum(1 for i in range(0, n, 1) if a[i] != b[i])
    return d + abs(len(a) - len(b))


def fastdiff(a, b):
    """count differing bytes, chunked so it is not glacial"""
    n = min(len(a), len(b))
    d = 0
    CH = 1 << 16
    for s in range(0, n, CH):
        x, y = a[s:s + CH], b[s:s + CH]
        if x != y:
            d += sum(1 for i in range(len(x)) if x[i] != y[i])
    return d + abs(len(a) - len(b))


def main():
    stream = open(os.path.join(WORK, 'blk01.lz'), 'rb').read()
    print('mapping literals under the shipped (V1) codec ...')
    dec, lit = map_literals_v1(stream)

    cases = {
        'version string 0x568 (the edit that FLASHED OK)':
            {o: b for o, b in zip(range(0x568, 0x56C), b'9.99')},
        'palette table 0xCC2A70, 11 entries (QUARANTINED - bricked)':
            {o: (0xFF if (o & 3) == 3 else (o * 37) & 0xFF)
             for o in range(0xCC2A70, 0xCC2A70 + 44)},
        'palette tables 0xCC2A70-0xCC5600 (full theme)':
            {o: (0xFF if (o & 3) == 3 else (o * 37) & 0xFF)
             for o in range(0xCC2A70, 0xCC5600)},
    }

    with tempfile.TemporaryDirectory() as tmp:
        base_v1 = decode(stream, 'v1', tmp)
        base_ok = decode(stream, 'oku', tmp)
        print(f'\nbaselines: V1 {len(base_v1):,} bytes, OKU {len(base_ok):,} bytes\n')
        print(f"{'edit':<56} {'literal':>8} {'V1 says':>10} {'OKU says':>10} {'ratio':>8}")
        print('-' * 96)
        for label, edits in cases.items():
            ed = {o: v for o, v in edits.items() if o in lit}
            buf = bytearray(stream)
            for o, v in ed.items():
                buf[lit[o]] = v
            p = bytes(buf)
            d1 = fastdiff(base_v1, decode(p, 'v1', tmp))
            d2 = fastdiff(base_ok, decode(p, 'oku', tmp))
            r = (d2 / d1) if d1 else float('inf')
            print(f'{label:<56} {len(ed):>8} {d1:>10,} {d2:>10,} {r:>8.1f}x')

    print("""
Read this as: two match semantics, the SAME stream edits, wildly different
amounts of collateral output change. Neither number is the deck's. The deck's
decompressor is a third semantics we do not have, so the real blast radius of a
palette patch is unmeasured -- not small, unmeasured.""")


if __name__ == '__main__':
    main()
