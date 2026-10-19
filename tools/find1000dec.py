#!/usr/bin/env python3
"""Walk bsr targets; print small functions containing the and#F0 sites."""
import sys, struct
sys.path.insert(0, "tools")
from shxray import branch_target

FLASH = open("/tmp/x1k_flash.bin", "rb").read()[:0x40000]
SITES = (0x26AD3, 0x2984E)


def walk(entry, limit=600):
    from capstone import Cs, CS_ARCH_SH, CS_MODE_LITTLE_ENDIAN
    cs = Cs(CS_ARCH_SH, CS_MODE_LITTLE_ENDIAN)
    code, seen, stack = {}, set(), [entry]
    bad = 0
    while stack and len(code) < limit:
        pc = stack.pop()
        while True:
            if pc in seen or len(code) >= limit or not (0 <= pc < len(FLASH) - 1):
                break
            seen.add(pc)
            w = struct.unpack_from("<H", FLASH, pc)[0]
            if w == 0xFFFF:
                bad += 1
                break
            try:
                ins = list(cs.disasm(FLASH[pc:pc + 2], pc))
                mn, op = (ins[0].mnemonic, ins[0].op_str) if ins else (".word", hex(w))
            except Exception:
                mn, op = ".word", hex(w)
            if mn == ".word":
                bad += 1
                if bad > 30:
                    return {}
            code[pc] = (mn, op)
            top = w >> 12
            if top in (0xA, 0xB):
                t = branch_target(pc, w)
                if not (0 <= t < len(FLASH)):
                    break
                if top == 0xB:
                    stack.append(pc + 2)
                pc = t
                continue
            if top == 0x8:
                t = branch_target(pc, w)
                if 0 <= t < len(FLASH):
                    stack.append(t)
                pc += 2
                continue
            if w == 0x000B:
                break
            if (w & 0xF0FF) in (0x400B, 0x402B) or w in (0x0023, 0x0003):
                break
            pc += 2
    return code


def main():
    targets = set()
    for i in range(0, len(FLASH) - 1, 2):
        w = struct.unpack_from("<H", FLASH, i)[0]
        if (w >> 12) == 0xB:
            d = w & 0xFFF
            if d & 0x800:
                d -= 0x1000
            t = (i + 4 + d * 2) & 0xFFFFFFFF
            if 0 <= t < len(FLASH):
                targets.add(t)
    print(len(targets), "targets", flush=True)
    for t in sorted(targets):
        code = walk(t)
        if not code:
            continue
        pcs = set(code)
        for s in SITES:
            if s in pcs:
                print(f"== func@{t:#x} contains {s:#x} ({len(code)} insns) ==")
                for pc in sorted(code):
                    print(f"  {pc:#x}: {code[pc][0]:10s} {code[pc][1]}")
                print()


if __name__ == "__main__":
    main()
