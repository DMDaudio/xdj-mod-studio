"""XDJ-700 firmware container: unpack / repack.

.UPD layout
    "<len_MAIN>\r\n" "<len_PANL>\r\n" [MAIN section][PANL section]
Each section
    <tag><"XDJ-700     " name "Ver" x.yz><pad> S0 record, then S2 records, then S7/S8.
S2 payload is a flat image; inside it, compressed blocks are
    [u32 le comp_len][01 00 ee ff][LZSS stream]
LZSS is Okumura: 4096-byte ring pre-filled 0x20, F=18, threshold 2,
control byte LSB-first, 1=literal, 0=(u16 offset/len pair).
"""
import re, struct

MAGIC = b'\x01\x00\xee\xff'
N, F, THR, FILL = 4096, 18, 2, 0x20


def crc16_xmodem(b):
    """Section trailer: CRC-16/XMODEM (poly 0x1021, init 0), stored LE."""
    c = 0
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


# ---------- S-records ----------

def srec_parse(text):
    """-> (list of (addr, data), start_rec_line, header_prefix)"""
    chunks, start = [], None
    for ln in text.split('\r\n'):
        for m in re.finditer(r'S([0-9])([0-9A-F]{2})([0-9A-F]+)', ln):
            t, cnt, rest = int(m.group(1)), int(m.group(2), 16), m.group(3)
            raw = bytes.fromhex(m.group(2) + rest[:cnt * 2])
            if (~sum(raw[:-1])) & 0xFF != raw[-1]:
                raise ValueError('bad S-record checksum')
            if t == 2:
                chunks.append((int(rest[:6], 16), bytes.fromhex(rest[6:-2])))
            elif t in (7, 8, 9):
                start = rest[:-2]
    return chunks, start


def srec_emit(addr, data):
    body = struct.pack('>I', addr)[1:] + data
    cnt = len(body) + 1
    raw = bytes([cnt]) + body
    return 'S2%02X%s%02X' % (cnt, body.hex().upper(), (~sum(raw)) & 0xFF)


def flatten(chunks):
    lo = min(a for a, _ in chunks)
    hi = max(a + len(d) for a, d in chunks)
    buf = bytearray(b'\xff' * (hi - lo))
    for a, d in chunks:
        buf[a - lo:a - lo + len(d)] = d
    return bytes(buf), lo


# ---------- LZSS ----------

def lzss_decompress(src):
    rb = bytearray([FILL] * N)
    r, out, p, n = 0, bytearray(), 0, len(src)
    while p < n:
        flags = src[p]; p += 1
        for b in range(8):
            if p >= n:
                return bytes(out)
            if (flags >> b) & 1:
                c = src[p]; p += 1
                out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
            else:
                if p + 1 >= n:
                    return bytes(out)
                b1, b2 = src[p], src[p + 1]; p += 2
                i = b2 | ((b1 & 0xF0) << 4)
                j = (b1 & 0x0F) + THR + 1
                for k in range(j):
                    c = rb[(i + k) & (N - 1)]
                    out.append(c); rb[r] = c; r = (r + 1) & (N - 1)
    return bytes(out)


def lzss_compress(data):
    """Greedy longest-match. Not byte-identical to Pioneer's encoder, but
    produces a stream their decompressor reproduces exactly."""
    rb = bytearray([FILL] * N)
    r, p, n = 0, 0, len(data)
    out, ctrl, items, bit = bytearray(), 0, [], 0
    idx = {}                                    # 3-byte key -> ring positions

    def flush():
        nonlocal ctrl, items, bit
        out.append(ctrl)
        for it in items:
            out.extend(it)
        ctrl, items, bit = 0, [], 0

    while p < n:
        best_len, best_off = 0, 0
        key = data[p:p + 3]
        if len(key) == 3:
            for cand in idx.get(bytes(key), ()):
                l = 0
                while l < min(F, n - p) and rb[(cand + l) & (N - 1)] == data[p + l]:
                    l += 1
                if l > best_len:
                    best_len, best_off = l, cand
                    if l == F:
                        break
        if best_len > THR:
            items.append(bytes([((best_off >> 4) & 0xF0) | (best_len - THR - 1),
                                best_off & 0xFF]))
            adv = best_len
        else:
            ctrl |= (1 << bit)
            items.append(bytes([data[p]]))
            adv = 1
        for k in range(adv):
            c = data[p + k]
            if r >= 2:
                idx.setdefault(bytes(rb[r - 2:r + 1]), []).append((r - 2) & (N - 1))
            rb[r] = c; r = (r + 1) & (N - 1)
        p += adv
        bit += 1
        if bit == 8:
            flush()
    if bit:
        flush()
    return bytes(out)


# ---------- blocks ----------

def find_blocks(img):
    out, i = [], 0
    while True:
        i = img.find(MAGIC, i)
        if i < 0:
            return out
        hdr = i - 4
        ln = struct.unpack('<I', img[hdr:hdr + 4])[0]
        if 0 < ln <= len(img) - (hdr + 8):
            out.append((hdr, ln))
        i += 4
