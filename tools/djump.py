#!/usr/bin/env python3
"""djump.py -- disassemble the NEW code block that reads the
'PIONEER/rekordbox/export.pdb' strings (pool refs 0xA8079578/0xA8079598 at
0x080BD1830/D34; the block starts ~0x080BD1822, right after the jsr 0x8D0C8028
at 0x080BD1822* pattern). Also the PGM-table consumer block at 0x0BA71AC.

Usage: djump.py [start_hex] [words]
"""
import struct
import sys

d = open("/tmp/dec4.bin", "rb").read()


def dis(addr, n=60):
    from capstone import Cs, CS_ARCH_SH, CS_MODE_LITTLE_ENDIAN, CS_MODE_SH4
    md = Cs(CS_ARCH_SH, CS_MODE_SH4 | CS_MODE_LITTLE_ENDIAN)
    print(f"--- {addr:#x} ({n} words) ---")
    for ins in md.disasm(d[addr - 0x08000000:addr - 0x08000000 + 2 * n],
                         addr):
        print(f"{ins.address:08x}  {ins.bytes.hex():<8} {ins.mnemonic} "
              f"{ins.op_str}")


if __name__ == "__main__":
    a = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x080BD1800
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    dis(a, n)
