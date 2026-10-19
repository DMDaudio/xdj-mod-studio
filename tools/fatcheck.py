#!/usr/bin/env python3
"""fatcheck.py -- independent audit of emu/usbdisk.py virtual FAT32 disks.

Walks MBR -> boot -> FATs -> all directories (LFN+short) -> full file
data via cluster chains. Reports anything a strict firmware parser
could choke on: broken chains, cross-linked clusters, size mismatches,
bad LFN checksums, out-of-range clusters, invalid entries.
Usage: fatcheck.py <folder>  (builds the same FolderDisk the deck sees)
"""
import os
import struct
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "emu"))

SECTOR = 512


def lfn_checksum(short11):
    s = 0
    for c in short11:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s


class Disk:
    def __init__(self, disk):
        self.d = disk
        self.problems = []

    def rd(self, lba, n=1):
        out = bytearray()
        for i in range(n):
            out += self.d.read(lba + i, 1)
        return bytes(out)

    def check(self):
        mbr = self.rd(0)
        assert mbr[510:512] == b"\x55\xaa", "no MBR magic"
        ptype, start, size = struct.unpack_from("<B3sB3sII", mbr, 446)[0], None, None
        import struct as S
        _, _, ptype, _, start, size = S.unpack_from("<B3sB3sII", mbr, 446)
        print(f"partition type={ptype:#x} start={start} sectors={size}")
        boot = self.rd(start)
        assert boot[510:512] == b"\x55\xaa", "no boot magic"
        (bps, spc, rsv, nfats, _, _, media, _, _, _, _, tot,
         ) = S.unpack_from("<HBHBHHBHHHII", boot, 11)
        fat_sz = S.unpack_from("<I", boot, 36)[0]
        root = S.unpack_from("<I", boot, 44)[0]
        print(f"bps={bps} spc={spc} rsv={rsv} fats={nfats} fat_sz={fat_sz} "
              f"root_clus={root}")
        self.base, self.fat0 = start, start + rsv
        self.data = start + rsv + nfats * fat_sz
        self.spc = spc
        self.nfat = (size - rsv - nfats * fat_sz) // spc
        print(f"data_lba={self.data} clusters~{self.nfat}")
        # FAT coherence: walk every chain from root
        fat = self.rd(self.fat0, fat_sz)
        self.fat = fat
        self.used = {}
        self.walk_dir(root, "/")
        print(f"dirs/files walked; {len(self.problems)} problems")
        for p in self.problems[:30]:
            print("  PROBLEM:", p)
        return not self.problems

    def chain(self, c0):
        seen = []
        c = c0
        while c < 0x0FFFFFF8:
            if c in self.used:
                self.problems.append(f"cross-linked cluster {c}")
                break
            if c < 2 or c >= 2 + self.nfat + 100:
                self.problems.append(f"cluster {c} out of range")
                break
            self.used[c] = True
            seen.append(c)
            v = struct.unpack_from("<I", self.fat, c * 4)[0] & 0x0FFFFFFF
            if v == 0:
                self.problems.append(f"chain ends in free cluster at {c}")
                break
            c = v
            if len(seen) > self.nfat + 10:
                self.problems.append("chain loop!")
                break
        return seen

    def walk_dir(self, c0, path):
        chain = self.chain(c0)
        raw = bytearray()
        for c in chain:
            lba = self.data + (c - 2) * self.spc
            raw += self.rd(lba, self.spc)
        # root has no ./.. but same parse
        off, pending = 0, []
        while off + 32 <= len(raw):
            e = raw[off:off + 32]
            off += 32
            if e[0] == 0x00:
                break
            if e[0] == 0xE5:
                continue
            attr = e[11]
            if attr == 0x0F:
                pending.append(bytes(e))
                continue
            if attr == 0x08:
                pending = []
                continue  # volume label
            name_s = bytes(e[0:11])
            ch = chain[0] if chain else -1
            if attr & 0x10:
                pending = []
                if name_s not in (b".          ", b"..         "):
                    nm = self.lfn_name(pending, name_s)
                    self.walk_dir(self.clus(e), path + nm + "/")
            else:
                size = struct.unpack_from("<I", e, 28)[0]
                nm = self.lfn_name(pending, name_s)
                fc = self.clus(e)
                if size:
                    fchain = self.chain(fc)
                    got = sum(1 for _ in fchain) * self.spc * SECTOR
                    import math
                    need = math.ceil(size / (self.spc * SECTOR))
                    if len(fchain) != need:
                        self.problems.append(
                            f"{path}{nm}: size {size} needs {need} clus, "
                            f"chain has {len(fchain)}")
                    # read every byte
                    left = size
                    for cc in fchain:
                        blk = self.rd(self.data + (cc - 2) * self.spc,
                                      self.spc)
                        left -= len(blk)
                    if left > 0:
                        self.problems.append(f"{path}{nm}: short read")
                pending = []

    def clus(self, e):
        import struct as S
        return (S.unpack_from("<H", e, 20)[0] << 16) | S.unpack_from("<H", e, 26)[0]

    def lfn_name(self, pending, short11):
        if not pending:
            return short11.decode("ascii", "replace").strip()
        parts = {}
        for e in pending:
            seq = e[0] & 0x1F
            parts[seq] = e[1:11] + e[14:26] + e[28:32]
            ck = e[13]
            if ck != lfn_checksum(short11):
                self.problems.append("LFN checksum mismatch")
        u = b"".join(parts[k] for k in sorted(parts))
        # parse as UTF-16 units: stop at first 0x0000, skip 0xFFFF pad
        chars = []
        for i in range(0, len(u) - 1, 2):
            w = u[i] | (u[i + 1] << 8)
            if w == 0x0000:
                break
            if w == 0xFFFF:
                continue
            chars.append(w)
        try:
            return "".join(chr(w) for w in chars)
        except Exception:
            self.problems.append("LFN decode fail")
            return "?"


def main():
    sys.path.insert(0, "emu")
    from usbdisk import FolderDisk
    d = FolderDisk(sys.argv[1])
    print(f"sectors={d.sectors} (~{d.sectors // 2048}MB)")
    ok = Disk(d).check()
    print("RESULT:", "CLEAN" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
