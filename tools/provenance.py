#!/usr/bin/env python3
"""provenance.py -- reach colours that are NOT literal-encoded.

    python3 tools/provenance.py F84A50            # trace a colour
    python3 tools/provenance.py F84A50 20E060     # and emit the patch

THE PROBLEM
tools/litpatch.py can only edit bytes the encoder stored as LZSS literals. Most
of the UI palette is literal, but some colours are not -- the QUANTIZE red
#F84A50 appears 9 times in block1 and every one of them is match-encoded.

THE IDEA
A match copies bytes from earlier output. Follow that chain far enough and every
byte bottoms out at a literal. Patch THAT literal and every copy downstream
changes with it. So a match-encoded colour is still reachable, just indirectly.

WORKED EXAMPLE (verified by re-decoding)
    #F84A50 at block1 0xCC3C42 -- all 4 bytes match-encoded
      byte 0 -> literal at stream 0x47CAC4 (2 hops)   feeds 29 output bytes
      byte 1 -> literal at stream 0x47CCBF (1 hop)    feeds 28
      byte 2 -> literal at stream 0x47C230 (6 hops)   feeds 43
      byte 3 -> literal at stream 0x47CE27 (0 hops)   feeds 18
    Patching 3 stream bytes turned all 9 occurrences green. Total output change:
    89 bytes across 56 runs -- 44 intended, ~45 stray collateral.

*** THIS IS RISKIER THAN PLAIN LITERAL PATCHING. READ THIS. ***
A plain literal patch is safe no matter how wrong our codec is: a literal decodes
to itself under ANY match semantics. Provenance patching is NOT -- it depends on
knowing which earlier bytes a match copies from, i.e. on the match semantics
being CORRECT. Ours are not proven (see CODEC.md). If they are wrong, this
patches the wrong literals and the result on hardware is unpredictable.

It also has collateral: bytes elsewhere that happen to descend from the same
literal change too. Always check the blast radius before shipping one.

Use plain litpatch where you can. Use this only when you cannot, and say so.
"""
import os, struct, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
N, F, THR, FILL = 4096, 18, 2, 0x20
LIMIT = 0xCC6000          # enough to cover the palette region; raise if needed


def load_stream(work=None):
    work = work or os.path.join(HERE, "..", "work")
    img = open(f"{work}/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    return bytearray(img[0x40008:0x40008 + ln])


def decode(s, limit=LIMIT, trace=False):
    """Decode with the SHIPPED codec (V1), optionally recording provenance."""
    rb = bytearray([FILL] * (N - F) + [0] * F)
    rbsrc = [None] * N
    r, p, n = N - F, 0, len(s)
    out = bytearray()
    kind = bytearray() if trace else None
    val = [] if trace else None
    while p < n and len(out) < limit:
        fl = s[p]; p += 1
        for b in range(8):
            if p >= n or len(out) >= limit:
                return (bytes(out), kind, val) if trace else bytes(out)
            if fl >> b & 1:
                c = s[p]
                out.append(c)
                if trace: kind.append(0); val.append(p)
                rb[r] = c; rbsrc[r] = len(out) - 1
                p += 1; r = (r + 1) % N
            else:
                if p + 1 >= n:
                    return (bytes(out), kind, val) if trace else bytes(out)
                b1, b2 = s[p], s[p + 1]; p += 2
                i = b2 | ((b1 & 0xF0) << 4); j = (b1 & 0x0F) + THR + 1
                for k in range(j):
                    q = (i + k) % N
                    c = rb[q]; sp = rbsrc[q]
                    out.append(c)
                    if trace: kind.append(1); val.append(sp if sp is not None else -1)
                    rb[r] = c; rbsrc[r] = len(out) - 1; r = (r + 1) % N
    return (bytes(out), kind, val) if trace else bytes(out)


def roots(kind, val, n):
    """output offset -> stream offset of the literal it descends from (-1 if none)"""
    R = [-1] * n
    for o in range(n):
        if kind[o] == 0:
            R[o] = val[o]
        else:
            s = val[o]
            R[o] = R[s] if 0 <= s < o and R[s] != -1 else -1
    return R


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    want = bytes.fromhex(sys.argv[1])
    new = bytes.fromhex(sys.argv[2]) if len(sys.argv) > 2 else None
    s = load_stream()
    out, kind, val = decode(s, trace=True)
    R = roots(kind, val, len(out))
    fan = collections.Counter(x for x in R if x != -1)

    pat = bytes([0xFF, want[2], want[1], want[0]])          # RGBA, little-endian
    sites = []
    i = -1
    while True:
        i = out.find(pat, i + 1)
        if i < 0: break
        sites.append(i)
    if not sites:
        sys.exit(f"#{want.hex().upper()} not found in the decoded block")
    print(f"#{want.hex().upper()}: {len(sites)} occurrence(s), first at {sites[0]:#09x}\n")

    edits = {}
    for k in range(4):
        o = sites[0] + k
        lit = R[o]
        direct = kind[o] == 0
        print(f"  byte {k}  out {o:#09x} = {out[o]:02X}  "
              + (f"LITERAL at stream {lit:#x}" if direct
                 else f"-> literal at stream {lit:#x}")
              + f"   feeds {fan.get(lit,0)} output bytes")
        if new is not None and lit >= 0:
            nb = bytes([0xFF, new[2], new[1], new[0]])[k]
            if s[lit] != nb:
                edits[lit] = nb

    if new is None:
        print("\n(pass a replacement colour to emit a patch)")
        return
    print(f"\npatching {len(edits)} stream byte(s) -> #{new.hex().upper()}")
    s2 = bytearray(s)
    for o, v in edits.items(): s2[o] = v
    after = decode(s2)
    diff = [i for i in range(min(len(out), len(after))) if out[i] != after[i]]
    npat = bytes([0xFF, new[2], new[1], new[0]])
    print(f"  target occurrences : {out.count(pat)} -> {after.count(pat)}")
    print(f"  new colour appears : {after.count(npat)} time(s)")
    print(f"  total bytes changed: {len(diff)}  "
          f"(intended {4*len(sites)}, collateral ~{len(diff)-4*len(sites)})")
    print(f"  stream edits       : " + ", ".join(f"{o:#x}={v:02X}" for o, v in sorted(edits.items())))


if __name__ == "__main__":
    main()
