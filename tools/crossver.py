#!/usr/bin/env python3
"""crossver.py -- recover KNOWN PLAINTEXT for one firmware from its siblings.

    crossver.py TARGET.img SOURCE.img [SOURCE.img ...]

THE IDEA
CODEC.md concluded the codec was unsolvable from files: every byte is either a
literal (decodes correctly under ANY codec, so it says nothing about the codec)
or match-produced (destroyed). "There is no third category."

There is. Pioneer shipped several firmware versions for this deck. They share
most of their content but were compressed independently, so the compressor made
DIFFERENT literal/match choices in each. Wherever a sibling stored something as
LITERALS that the target stored as a MATCH, the sibling gives us the target's
true output for that match. That is known plaintext, and a match whose correct
output is known is a direct equation for the offset/length semantics.

METHOD
 1. Decode both; keep the literal map. (Codec-independent: a literal costs one
    stream byte and a match two, always. Only WHERE it lands depends on the
    unsolved semantics.)
 2. Take maximal literal runs >= 32 bytes and index the source's by their first
    32 bytes, keeping only unique signatures.
 3. Match against the target's runs -> alignment anchors.
 4. Extend each anchor outward. Where both are literal the bytes MUST agree --
    that validates the alignment and stops it at insertions. Where the source is
    literal and the target is a match, the source byte is the TRUTH.

Constraints are emitted as (stream offset of the anchoring literal, delta, byte)
which is codec-INDEPENDENT: output offsets shift with the length formula,
stream literal positions never do.
"""
import os, struct, sys, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from litpatch import map_literals

RUN = 32


def load(path):
    img = open(path, "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    dec, lit = map_literals(img[0x40008:0x40008 + ln])
    mask = bytearray(len(dec))
    for o in lit:
        mask[o] = 1
    return dec, lit, bytes(mask)


def runs(mask, mn=RUN):
    out = []; i = 0; n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]: j += 1
            if j - i >= mn: out.append((i, j))
            i = j
        else: i += 1
    return out


def recover(tdec, tmask, sdec, smask, span=4096):
    """-> {target output offset: true byte} from one sibling."""
    idx = {}
    for a, b in runs(smask):
        idx.setdefault(sdec[a:a + RUN], []).append(a)
    uniq = {k: v[0] for k, v in idx.items() if len(v) == 1}
    known = {}; agree = 0; anchors = 0
    for a, b in runs(tmask):
        p = uniq.get(tdec[a:a + RUN])
        if p is None: continue
        anchors += 1
        for step in (1, -1):
            k = 0
            while True:
                k += step
                if abs(k) > span: break
                i, j = a + k, p + k
                if i < 0 or j < 0 or i >= len(tdec) or j >= len(sdec): break
                ti, sj = tmask[i], smask[j]
                if ti and sj:
                    if tdec[i] != sdec[j]: break
                    agree += 1
                elif sj and not ti:
                    known[i] = sdec[j]
    return known, agree, anchors


def constraints(known, lit, dec, maxdelta=64):
    """Re-key output offsets to (stream literal offset, delta, byte)."""
    lits = sorted(lit.items())
    outs = [a for a, _ in lits]
    cons = []
    for o, truth in known.items():
        k = bisect.bisect_right(outs, o) - 1
        if k < 0: continue
        L, s = lits[k]
        d = o - L
        if 0 < d <= maxdelta:
            cons.append((s, d, truth))
    return cons


if __name__ == "__main__":
    tgt = sys.argv[1]; srcs = sys.argv[2:]
    tdec, tlit, tmask = load(tgt)
    print(f"target {tgt}: {len(tdec):,} out, {100*len(tlit)/len(tdec):.1f}% literal")
    allknown = {}; conflicts = 0
    for s in srcs:
        sdec, slit, smask = load(s)
        k, agree, anch = recover(tdec, tmask, sdec, smask)
        for o, v in k.items():
            if o in allknown and allknown[o] != v: conflicts += 1
            allknown[o] = v
        print(f"  {os.path.basename(os.path.dirname(s)) or s}: {anch:,} anchors, "
              f"{agree:,} agreeing, {len(k):,} known  (union {len(allknown):,})")
    wrong = sum(1 for o, v in allknown.items() if tdec[o] != v)
    print(f"\nunion known plaintext : {len(allknown):,}")
    print(f"conflicts             : {conflicts:,}")
    print(f"our decoder wrong on  : {wrong:,} ({100*wrong/max(len(allknown),1):.1f}%)")
    cons = constraints(allknown, tlit, tdec)
    print(f"usable constraints    : {len(cons):,}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "crossver", "constraints.bin")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        for s, d, b in cons:
            f.write(struct.pack("<IBB", s, d, b))
    print(f"wrote {out}")
    # contiguity: the real prize
    ks = sorted(allknown)
    best = cur = 1; start = bs = ks[0] if ks else 0
    for i in range(1, len(ks)):
        if ks[i] == ks[i-1] + 1: cur += 1
        else:
            if cur > best: best, bs = cur, start
            cur = 1; start = ks[i]
    if cur > best: best, bs = cur, start
    print(f"longest CONTIGUOUS known run: {best} bytes at {bs:#x}")
