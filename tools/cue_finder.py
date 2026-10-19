#!/usr/bin/env python3
"""Find candidate cue/jog handler functions offline.

SH code loads 32-bit addresses via PC-relative literal pools:
  mov.l @(disp,PC),Rn   ; Rn = *(PC+4+disp)
  ... literal pool ...  ; .long target_addr
So a function using UI table base 0x0812xxxx has that constant in a
literal pool shortly after a mov.l opcode (0xD000-0xDFFF).

This script lists every occurrence of UI-table-base pointers and the
nearest preceding RTS (0x000B) + plausible function prologue, giving
Ghidra jump targets for cue/jog RE.
Usage: python3 tools/cue_finder.py work/MAIN.blk01.bin
"""
import struct
import sys

BASE = 0x08000000
LOW, HIGH = 0x08120000, 0x08130000


def main(path):
    d = open(path, "rb").read()
    refs = []
    for i in range(0, len(d) - 4, 2):
        v = struct.unpack_from("<I", d, i)[0]
        if LOW <= v < HIGH:
            refs.append((i, v))
    print(f"# {len(refs)} refs into {LOW:#x}-{HIGH:#x}")
    for off, tgt in refs[:60]:
        # nearest RTS before off (function end of previous fn)
        rts = d.rfind(b"\x0b\x00", 0, off)
        # nearest mov.l opcode Dxxx in 1KB before literal (the load site)
        load = None
        for j in range(max(0, off - 1024), off - 1):
            op = struct.unpack_from("<H", d, j)[0]
            if 0xD000 <= op <= 0xDFFF:
                disp = (op & 0xFF) * 4
                lit = (j + 4 + disp)
                if lit == off or lit == off - 2:
                    load = j
                    break
        print(f"lit @{off:#09x} (addr {BASE+off:#x}) -> {tgt:#x} "
              f"prev_rts @{rts:#x} load_site @{load if load is None else hex(load)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "work/MAIN.blk01.bin")
