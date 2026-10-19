#!/usr/bin/env python3
"""XDJ-700 firmware container: exact parse / edit / rebuild.

    fwtool.py selftest FW.UPD          pass-through must be BYTE-IDENTICAL
    fwtool.py build FW.UPD OUT.UPD MOD.py

Uses tools/lzss_boot.py -- the codec transcribed from the deck's own
decompressor. Block framing is [u32 len][stream len bytes][u16 sum], stream
starting at base+4, sum over [base, base+4+len). See CODEC.md.
"""
import os, re, struct, sys, importlib.util
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lzss_boot as L

MAGIC = b'\x01\x00\xee\xff'


def crc16_xmodem(b):
    c = 0
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


class Section:
    """One .UPD section, parsed so it can be re-emitted byte-for-byte."""
    def __init__(self, blob):
        self.blob = blob
        text = blob.decode('latin1')
        first = re.search(r'S2', text).start()
        self.pre = text[:first]
        self.recs = []                       # (addr, nbytes)
        body_end = None
        for m in re.finditer(r'S2([0-9A-F]{2})([0-9A-F]{6})([0-9A-F]*?)([0-9A-F]{2})(?=\r\n)', text):
            cnt = int(m.group(1), 16)
            self.recs.append((int(m.group(2), 16), cnt - 4))
            body_end = m.end() + 2      # past the record's CRLF
        self.post = text[body_end:-1]   # terminator; last 2 B are the CRC
        self.post = text[body_end:len(text) - 2]
        chunks = []
        off = first
        for (a, n) in self.recs:
            mm = re.compile(r'S2[0-9A-F]{2}%06X([0-9A-F]{%d})[0-9A-F]{2}' % (a, n * 2))
            m = mm.search(text, off)
            chunks.append((a, bytes.fromhex(m.group(1))))
            off = m.end()
        lo = min(a for a, _ in chunks)
        hi = max(a + len(d) for a, d in chunks)
        img = bytearray(b'\xff' * (hi - lo))
        for a, d in chunks:
            img[a - lo:a - lo + len(d)] = d
        self.img, self.base = bytearray(img), lo
        m = re.search(r'XDJ-700\s+(\w+)Ver', text)
        self.name = m.group(1) if m else '?'

    def emit(self):
        out = [self.pre]
        for a, n in self.recs:
            d = self.img[a - self.base:a - self.base + n]
            body = struct.pack('>I', a)[1:] + bytes(d)
            cnt = len(body) + 1
            raw = bytes([cnt]) + body
            out.append('S2%02X%s%02X\r\n' % (cnt, body.hex().upper(), (~sum(raw)) & 0xFF))
        body = (''.join(out) + self.post).encode('latin1')
        return body + struct.pack('<H', crc16_xmodem(body))

    def blocks(self):
        """Find blocks the way the boot stub validates them: a u32 length whose
        byte sum over [base, base+4+len) equals the u16 stored at base+4+len.
        The 01 00 EE FF that earlier tooling keyed on is not a magic number --
        it appears nowhere in the firmware and is merely what Pioneer's
        compressor emits for 19 leading zeros."""
        import numpy as np
        a = np.frombuffer(bytes(self.img), dtype=np.uint8).astype(np.int64)
        c = np.concatenate(([0], np.cumsum(a)))
        n = len(a)
        ln = np.frombuffer(bytes(self.img[:n // 4 * 4]), dtype='<u4')
        pos = np.arange(len(ln)) * 4
        end = pos + 4 + ln
        ok = (ln > 1024) & (end + 2 <= n)
        idx = np.flatnonzero(ok)
        if len(idx) == 0:
            return []
        e = end[idx]
        got = ((c[e] - c[pos[idx]]) & 0xFFFF).astype(np.uint32)
        want = (a[e] | (a[e + 1] << 8)).astype(np.uint32)
        hit = idx[got == want]
        out, last = [], -1
        for i in hit:
            h = int(pos[i])
            if h <= last:
                continue
            out.append((h, int(ln[i])))
            last = h + 4 + int(ln[i])
        return out

    def check_blocks(self):
        r = []
        for hdr, ln in self.blocks():
            s = sum(self.img[hdr:hdr + 4 + ln]) & 0xFFFF
            st = struct.unpack_from('<H', self.img, hdr + 4 + ln)[0]
            r.append((hdr, ln, s, st, s == st))
        return r

    def write_block(self, hdr, old_ln, stream):
        """Install a new stream at hdr, keeping the image length constant.

        Refuses rather than crashing if the stream will not fit. Growing past
        the end of the image would move the checksum outside the buffer, and
        growing over live data would corrupt whatever follows.
        """
        end_old = hdr + 4 + old_ln + 2
        need = 4 + len(stream) + 2
        end_new = hdr + need
        if end_new > len(self.img):
            raise ValueError(
                'block %#x does not fit: needs %d bytes, image ends %d bytes '
                'short. Recompressed stream is %+d vs stock -- the encoder must '
                'at least match Pioneer\'s ratio.'
                % (hdr, need, end_new - len(self.img), len(stream) - old_ln))
        if end_new > end_old:
            tail = bytes(self.img[end_old:end_new])
            if any(b != 0xFF for b in tail):
                raise ValueError(
                    'block %#x would overwrite live data (+%d bytes past the '
                    'stock end)' % (hdr, end_new - end_old))
        struct.pack_into('<I', self.img, hdr, len(stream))
        self.img[hdr + 4:hdr + 4 + len(stream)] = stream
        s = sum(self.img[hdr:hdr + 4 + len(stream)]) & 0xFFFF
        struct.pack_into('<H', self.img, hdr + 4 + len(stream), s)
        for i in range(end_new, end_old):
            self.img[i] = 0xFF


def parse(path):
    raw = open(path, 'rb').read()
    e1 = raw.index(b'\r\n') + 2
    e2 = raw.index(b'\r\n', e1) + 2
    lens = [int(raw[:e1 - 2]), int(raw[e1:e2 - 2])]
    secs, off = [], e2
    for n in lens:
        secs.append(Section(raw[off:off + n]))
        off += n
    assert off == len(raw), 'trailing bytes'
    return raw, secs


def assemble(secs):
    blobs = [s.emit() for s in secs]
    hdr = ''.join('%d\r\n' % len(b) for b in blobs).encode('latin1')
    return hdr + b''.join(blobs)


def main():
    cmd = sys.argv[1]
    raw, secs = parse(sys.argv[2])
    for s in secs:
        print(f"section {s.name}: image {len(s.img):,} B @ {s.base:#x}, "
              f"{len(s.recs):,} S2 records")
        for hdr, ln, a, b, ok in s.check_blocks():
            print(f"   block @{hdr:#08x} len {ln:,}  sum {a:#06x} vs {b:#06x} "
                  f"{'OK' if ok else 'BAD'}")
    if cmd == 'selftest':
        out = assemble(secs)
        same = out == raw
        print(f"\npass-through: {len(out):,} vs {len(raw):,} bytes -- "
              f"{'BYTE-IDENTICAL' if same else 'DIFFERS'}")
        if not same:
            i = next(i for i, (x, y) in enumerate(zip(out, raw)) if x != y)
            print(f"  first difference at {i:#x}: {out[i:i+16].hex()} vs {raw[i:i+16].hex()}")
        return 0 if same else 1
    if cmd == 'build':
        spec = importlib.util.spec_from_file_location('mod', sys.argv[4])
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        for s in secs:
            if s.name != 'MAIN':
                continue
            for hdr, ln in s.blocks():
                dec = L.decode(bytes(s.img[hdr + 4:hdr + 4 + ln]))
                new = mod.apply(bytearray(dec), hdr)
                if bytes(new) == dec:
                    print(f"   block @{hdr:#08x}: unchanged"); continue
                nch = sum(1 for a, b in zip(dec, new) if a != b)
                st = L.encode(bytes(new))
                assert L.decode(st) == bytes(new), 'encoder round-trip failed'
                s.write_block(hdr, ln, st)
                print(f"   block @{hdr:#08x}: {nch:,} bytes changed, "
                      f"stream {ln:,} -> {len(st):,} ({len(st)-ln:+,})")
            for h, l, a, b, ok in s.check_blocks():
                print(f"   verify @{h:#08x}: sum {a:#06x} vs {b:#06x} {'OK' if ok else 'BAD'}")
        open(sys.argv[3], 'wb').write(assemble(secs))
        print(f"\nwrote {sys.argv[3]}")
        return 0


if __name__ == '__main__':
    sys.exit(main())
