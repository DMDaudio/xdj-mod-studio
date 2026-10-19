"""XDJ-700 LZSS — transcribed from the boot stub at MAIN.img 0x0900-0x09C2.

Ground truth, not inference. See CODEC.md.
  ring   4096 B at 0x0FFFD340, PRE-FILLED 0x20 for the first N-F=4078 bytes
  r0     4078
  flags  LSB first, 8 per byte, bit SET = literal
  match  i = b1 | ((b2 & 0xF0) << 4)      absolute ring index
         L = (b2 & 0x0F) + 3              3..18
  stream starts at block_base+4 and is `len` bytes long (the leading
  01 00 EE FF is STREAM DATA, not a magic header)
"""
N, F, THR = 4096, 18, 2
R0 = N - F

def decode(src):
    rb = bytearray([0x20]) * R0 + bytearray(N - R0)
    r, out, p, n = R0, bytearray(), 0, len(src)
    while p < n:
        fl = src[p]; p += 1
        for _ in range(8):
            if p >= n: return bytes(out)
            if fl & 1:
                c = src[p]; p += 1
                out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
            else:
                if p + 1 >= n: return bytes(out)
                b1, b2 = src[p], src[p + 1]; p += 2
                i = b1 | ((b2 & 0xF0) << 4)
                for k in range((b2 & 0x0F) + THR + 1):
                    c = rb[(i + k) & (N - 1)]
                    out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
            fl >>= 1
    return bytes(out)

def encode(data, lazy=True):
    """Okumura-compatible encoder. decode(encode(x)) == x, byte-exact.

    Greedy alone loses to Pioneer's compressor on some inputs. Lazy matching
    (emit a literal when position p+1 has a strictly longer match) recovers
    several KB on an 18 MB payload, which is the difference between fitting the
    flash slot and not.
    """
    PRE = bytes(N - R0) + b'\x20' * R0          # ring seen as history
    V = PRE + data
    nV, base = len(V), len(PRE)
    head = {}
    for j in range(base - 2):
        head.setdefault(V[j:j+3], []).append(j)

    def best_at(p):
        """-> (length, distance) of the longest match at p, or (0, 0)."""
        if p + 2 >= nV:
            return 0, 0
        bl, bd = 0, 0
        for j in head.get(V[p:p+3], ())[-1:-4097:-1]:
            d = p - j
            if d > N:
                break
            l = 3
            while l < F and p + l < nV and V[j + l] == V[p + l]:
                l += 1
            if l > bl:
                bl, bd = l, d
                if l == F:
                    break
        return bl, bd

    out = bytearray(); flags = 0; nf = 0; chunk = bytearray()
    p = base
    pend = None                                   # carried match from lookahead
    while p < nV:
        if pend is not None:
            bl, bd = pend; pend = None
        else:
            bl, bd = best_at(p)
        if lazy and bl >= 3 and bl < F:
            nl, nd = best_at(p + 1)
            if nl > bl:                           # better to start one later
                bl, bd, pend = 0, 0, (nl, nd)
        if bl >= 3:
            r = (R0 + p - base) & (N - 1)
            i = (r - bd) & (N - 1)
            chunk += bytes((i & 0xFF, ((i >> 4) & 0xF0) | (bl - THR - 1)))
            step = bl
        else:
            flags |= 1 << nf
            chunk.append(V[p]); step = 1
        nf += 1
        if nf == 8:
            out.append(flags); out += chunk
            flags = 0; nf = 0; chunk = bytearray()
        for k in range(p, min(p + step, nV - 2)):
            c = head.setdefault(V[k:k+3], [])
            c.append(k)
            if len(c) > 8192:
                del c[:4096]
        p += step
    if nf:
        out.append(flags); out += chunk
    return bytes(out)
