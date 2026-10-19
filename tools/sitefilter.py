#!/usr/bin/env python3
"""sitefilter.py -- keep only the edit sites whose changes never reach code.

    from sitefilter import safe_sites
    good, stats = safe_sites(src, dec, lit, sites)

WHY
Editing a literal changes every output byte that descends from it, and those
descendants are NOT confined to the region the edit starts in. A raw
propagation count cannot tell you whether that matters; what matters is WHERE
the changed bytes land. Inverting all literals in 225 code-free windows looked
reasonable by region and turned out to propagate to 11.4 million bytes, 76% of
the image, straight through code.

So: tag each candidate site, decode once while carrying tags through the ring,
and drop any site with a descendant inside a code-bearing 64 KB window. Code
windows are identified against the uncompressed boot stub as ground truth
(rts=376, sts.l pr=167 per 64K halfwords).

This is codec-independent in the way that matters: we do not need to know WHERE
a literal lands, only that the deck will substitute one colour byte for another
everywhere it lands. The landing map is our model's, so it is a guide, not a
guarantee -- but a site that is clean under both candidate decoders is a much
better bet than one that is not.
"""
N, F, THR, FILL = 4096, 18, 2, 0x20
W = 1 << 16


def code_windows(dec):
    out = set()
    for a in range(0, len(dec), W):
        e = min(a + W, len(dec)); rts = sts = 0
        for q in range(a, e - 1, 2):
            w = dec[q] | (dec[q + 1] << 8)
            if w == 0x000B: rts += 1
            elif w == 0x4F22: sts += 1
        hw = (e - a) // 2
        if (rts * 65536 // hw) >= 40 or (sts * 65536 // hw) >= 20:
            out.add(a)
    return out


def safe_sites(src, dec, lit, sites, width=2):
    """sites: output offsets. -> (clean sites, stats)"""
    code = code_windows(dec)
    owner = {}
    for idx, s in enumerate(sites):
        for k in range(width):
            if s + k in lit:
                owner[lit[s + k]] = idx
    rb = bytearray([FILL] * (N - F) + [0] * F)
    rtag = [-1] * N
    hits = [0] * len(sites); bad = [0] * len(sites)
    r = p = out = 0; r = N - F; n = len(src)
    while p < n:
        fl = src[p]; p += 1
        for b in range(8):
            if p >= n: break
            if fl >> b & 1:
                t = owner.get(p, -1)
                rb[r] = src[p]; rtag[r] = t
                if t >= 0:
                    hits[t] += 1
                    if (out // W) * W in code: bad[t] += 1
                p += 1; out += 1; r = (r + 1) % N
            else:
                if p + 1 >= n: break
                b1, b2 = src[p], src[p + 1]; p += 2
                i = b2 | ((b1 & 0xF0) << 4); j = (b1 & 0x0F) + THR + 1
                for k in range(j):
                    q = (i + k) % N
                    rb[r] = rb[q]; t = rtag[q]; rtag[r] = t
                    if t >= 0:
                        hits[t] += 1
                        if (out // W) * W in code: bad[t] += 1
                    out += 1; r = (r + 1) % N
    good = [s for idx, s in enumerate(sites) if bad[idx] == 0]
    stats = dict(total=len(sites), kept=len(good),
                 dropped=len(sites) - len(good),
                 changed=sum(hits[i] for i, s in enumerate(sites) if bad[i] == 0),
                 code_bytes_avoided=sum(bad))
    return good, stats
