#!/usr/bin/env python3
"""Disassemble a function by recursive descent (pool-safe).
Usage: dis1000.py [flashfile] addr [limit]  (addr = file offset = vaddr)."""
import sys, struct
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shxray import branch_target  # reuse branch math only

args = [a for a in sys.argv[1:] if not a.startswith("-")]
IMG = open(args[0], "rb").read() if len(args) > 2 else open("/tmp/x1k_flash.bin", "rb").read()
ARGS = args[1:] if len(args) > 2 else args
import os
BASE = int(os.environ.get("DISBASE", "0"), 0)


def dis(entry, limit=3000):
    from capstone import Cs, CS_ARCH_SH, CS_MODE_LITTLE_ENDIAN, CS_MODE_SH4
    cs = Cs(CS_ARCH_SH, CS_MODE_SH4 | CS_MODE_LITTLE_ENDIAN)
    code, seen, stack = {}, set(), [entry]
    while stack and len(code) < limit:
        pc = stack.pop()
        while True:
            if pc in seen or len(code) >= limit:
                break
            off = pc - BASE
            if not (0 <= off < len(IMG) - 1):
                break
            seen.add(pc)
            w = struct.unpack_from("<H", IMG, off)[0]
            try:
                ins = list(cs.disasm(IMG[off:off + 2], pc))
                mn, op = (ins[0].mnemonic, ins[0].op_str) if ins else (".word", hex(w))
            except Exception:
                mn, op = ".word", hex(w)
            # resolve mov.l @(disp,PC) pools to values
            if (w >> 12) == 0xD:
                pa = (((pc + 4) & ~3) + (w & 0xFF) * 4)
                po = pa - BASE
                if 0 <= po < len(IMG) - 3:
                    v = struct.unpack_from("<I", IMG, po)[0]
                    op = f"{op} ; [{pa:#x}]={v:#x}"
            code[pc] = (mn, op)
            top = w >> 12
            if top in (0xA, 0xB):
                t = branch_target(pc, w)
                if top == 0xB:
                    stack.append(pc + 2)
                pc = t
                continue
            if top == 0x8:
                stack.append(branch_target(pc, w))
                pc += 2
                continue
            if w == 0x000B:
                break
            if (w & 0xF0FF) in (0x400B, 0x402B) or w in (0x0023, 0x0003):
                break
            pc += 2
    for pc in sorted(code):
        print(f"{pc:#x}: {code[pc][0]:10s} {code[pc][1]}")


if __name__ == "__main__":
    dis(int(ARGS[0], 0), int(ARGS[1]) if len(ARGS) > 1 else 3000)
