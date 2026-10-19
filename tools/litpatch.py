#!/usr/bin/env python3
"""Literal-only patching: edit decompressed bytes WITHOUT recompressing.

Why this exists. Our LZSS match decoding is strongly evidenced but not proven
byte-exact against the deck's decompressor (see README, "The charset anomaly").
If it is subtly wrong, anything we recompress would decode differently on the
deck -- a brick risk.

Literals are immune to that. A literal byte in an LZSS stream decodes to itself
under ANY match semantics. So if every output byte we want to change was
emitted as a literal, we can patch those bytes directly in the compressed
stream: same stream length, no recompression, no layout change, and the deck
reproduces our edit exactly regardless of how it resolves matches.

map_literals() returns {output_offset: compressed_offset} for literal bytes.
patch() refuses any edit touching a match-produced byte.
"""
N, F, THR, FILL = 4096, 18, 2, 0x20


def map_literals(src):
    """-> (decompressed bytes, {out_off: comp_off} for literal-emitted bytes)"""
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


def patch(src, edits):
    """edits: {out_offset: new_byte}. Returns a new compressed stream of the
    SAME length. Raises if any target byte was not emitted as a literal."""
    dec, lit = map_literals(src)
    bad = [o for o in edits if o not in lit]
    if bad:
        raise ValueError(
            'not literal-encoded at output offsets ' +
            ', '.join(hex(o) for o in sorted(bad)[:8]) +
            ' -- refusing (would need recompression)')
    buf = bytearray(src)
    for o, v in edits.items():
        buf[lit[o]] = v
    return bytes(buf), dec


def verify(orig_src, new_src, edits, orig_dec=None):
    """Decode both and confirm every changed byte is an intended substitution.

    Changes propagate: if edited bytes are themselves the source of a later
    LZSS match, every copy changes too. That is correct -- those copies are the
    same text -- but it makes the diff a superset of `edits`. So rather than
    demand an exact match, require that every changed byte performs one of the
    intended (old -> new) substitutions, and that every edit actually took.
    """
    a = orig_dec if orig_dec is not None else map_literals(orig_src)[0]
    b, _ = map_literals(new_src)
    if len(a) != len(b):
        return False, f'length changed {len(a)} -> {len(b)}'
    subs = {(a[o], v) for o, v in edits.items()}
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    bad = [i for i in diff if (a[i], b[i]) not in subs]
    if bad:
        return False, (f'{len(bad)} of {len(diff)} changed bytes are not an '
                       f'intended substitution (first at {bad[0]:#x}: '
                       f'{a[bad[0]]:#04x} -> {b[bad[0]]:#04x})')
    for o, v in edits.items():
        if b[o] != v:
            return False, f'edit at {o:#x} did not take'
    extra = len(diff) - len(edits)
    return True, (f'{len(diff)} bytes changed ({len(edits)} direct'
                  + (f', {extra} propagated to match-copies' if extra else '')
                  + '), all intended substitutions')
