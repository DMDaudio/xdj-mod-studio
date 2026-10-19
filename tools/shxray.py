#!/usr/bin/env python3
"""shxray: recursive-descent SH-4 explorer over the unpacked image.

Usage:
  .venv/bin/python tools/shxray.py callers 0x08DFD09C
  .venv/bin/python tools/shxray.py func 0x08C5B970
  .venv/bin/python tools/shxray.py refs 0xFFE20000   (pools + GBR-relative)

Image: /tmp/dec4.bin (block1 decoded, base 0x08000000). Falls back to
decoding work/MAIN.img block1 on the fly if the cache is missing.
"""
import os
import struct
import sys

BASE = 0x08000000
REF = "/tmp/dec4.bin"

try:
    from capstone import Cs, CS_ARCH_SH, CS_MODE_SH4, CS_MODE_LITTLE_ENDIAN
except ImportError:
    Cs = None


def load_image():
    if os.path.exists(REF):
        return open(REF, "rb").read()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, "tools")
    import lzss_boot as L
    img = open("work/MAIN.img", "rb").read()
    ln = struct.unpack_from("<I", img, 0x40000)[0]
    blk = img[0x40000:0x40000 + ln + 8]
    dec = L.decode(blk[4:])
    open(REF, "wb").write(dec)
    return dec


IMG = load_image()
CS = Cs(CS_ARCH_SH, CS_MODE_SH4 | CS_MODE_LITTLE_ENDIAN) if Cs else None


def u16(off):
    if 0 <= off < len(IMG) - 1:
        return struct.unpack_from("<H", IMG, off)[0]
    return None


def dis_one(vaddr):
    """(mnemonic, op_str, size). size is always 2 (SH fixed)."""
    off = vaddr - BASE
    w = u16(off)
    if w is None:
        return "?", "", 2
    if CS is not None:
        try:
            ins = list(CS.disasm(IMG[off:off + 2], vaddr))
            if ins:
                return ins[0].mnemonic, ins[0].op_str, 2
        except Exception:
            pass
    return ".word", hex(w), 2


def branch_target(vaddr, w):
    top = w >> 12
    if top in (0xA, 0xB):  # bra/bsr disp12
        d = w & 0xFFF
        if d & 0x800:
            d -= 0x1000
        return (vaddr + 4 + d * 2) & 0xFFFFFFFF
    if top == 0x8:  # bt/bf disp8
        d = w & 0xFF
        if d & 0x80:
            d -= 0x100
        return (vaddr + 4 + d * 2) & 0xFFFFFFFF
    return None


def pool_target(vaddr, w):
    # mov.l @(disp,PC),Rn -> value at pool
    top = w >> 12
    if top in (0xD,):
        n = (w >> 8) & 0xF
        d = w & 0xFF
        pa = (((vaddr + 4) & ~3) + d * 4)
        off = pa - BASE
        if 0 <= off < len(IMG) - 4:
            return n, struct.unpack_from("<I", IMG, off)[0]
    if top in (0x9,):
        pass  # mov.w: small immediates, skip
    return None


def walk(entry, limit=20000):
    """Recursive descent from entry (virtual addr). Returns dict pc->(mn,op)
    plus lists of (src, dst, kind) edges."""
    code, edges = {}, []
    stack = [entry & 0xFFFFFFFF]
    seen = set()
    while stack and len(code) < limit:
        pc = stack.pop()
        while True:
            if pc in seen or len(code) >= limit:
                break
            seen.add(pc)
            off = pc - BASE
            w = u16(off)
            if w is None:
                break
            mn, op, _ = dis_one(pc)
            code[pc] = (mn, op)
            top = w >> 12
            if top in (0xA, 0xB):
                t = branch_target(pc, w)
                edges.append((pc, t, "bsr" if top == 0xB else "bra"))
                if top == 0xB:
                    stack.append((pc + 2) & 0xFFFFFFFF)  # return continues
                pc = t
                continue
            if top == 0x8:
                t = branch_target(pc, w)
                edges.append((pc, t, "bt/bf"))
                stack.append(t)
                pc = (pc + 2) & 0xFFFFFFFF
                continue
            if w == 0x000B:  # rts
                break
            # jsr @Rn / jmp @Rn / braf / bsrf: record, stop linear flow
            if (w & 0xF0FF) in (0x400B, 0x402B) or w in (0x0023, 0x0003) or \
                    (w & 0xFF00) in (0x2300, 0x0300):
                edges.append((pc, None, "indirect"))
                # delay slot executes; target unknown: stop this path
                break
            pc = (pc + 2) & 0xFFFFFFFF
    return code, edges


def func_at(entry, radius=200):
    # pool-aware: pre-scan for mov.l @(disp,PC) targets in walk range and
    # show them as data, not code.
    pools = set()
    for off in range(max(0, entry - BASE - 0x400), min(len(IMG), entry - BASE + 0x800), 2):
        w = u16(off)
        if w is not None and (w >> 12) == 0xD:
            pa = ((((BASE + off) + 4) & ~3) + (w & 0xFF) * 4)
            pools.add(pa)
    code, edges = walk(entry)
    out = []
    for pc in sorted(code):
        if pc in pools:
            off = pc - BASE
            v = struct.unpack_from("<I", IMG, off)[0] if 0 <= off < len(IMG) - 4 else 0
            out.append(f"  {pc:#010x}  .long     {v:#010x}")
            continue
        mn, op = code[pc]
        mark = " <== entry" if pc == entry else ""
        out.append(f"  {pc:#010x}  {mn:<8} {op}{mark}")
        if len(out) > radius * 2:
            break
    out.append(f".. {len(code)} insns, {len(edges)} edges")
    for s, d, k in edges[:20]:
        out.append(f"  edge {s:#x} -{k}-> {d if d is None else hex(d)}")
    return "\n".join(out)


def find_pools(value, lo=0, hi=None):
    """Find literal-pool words equal to value; return list of pool addrs."""
    import struct as S
    hi = len(IMG) if hi is None else hi
    out = []
    for off in range(lo, hi - 3, 4):
        if S.unpack_from("<I", IMG, off)[0] == (value & 0xFFFFFFFF):
            out.append(BASE + off)
    return out


def find_pool_users(pool_addr, lo=0, hi=None):
    """Find mov.l @(disp,PC) loading from pool_addr."""
    hi = len(IMG) if hi is None else hi
    out = []
    for off in range(lo, hi, 2):
        w = u16(off)
        if w is None:
            continue
        if (w >> 12) == 0xD:
            pa = ((((BASE + off) + 4) & ~3) + (w & 0xFF) * 4)
            if pa == pool_addr:
                out.append(BASE + off)
    return out


def find_bsr_to(target, lo=0, hi=None):
    """Find bsr/bra/bf/bt with computed target == target (direct calls)."""
    hi = len(IMG) if hi is None else hi
    out = []
    for off in range(lo, hi, 2):
        w = u16(off)
        if w is None:
            continue
        top = w >> 12
        t = None
        if top in (0xA, 0xB):
            d = w & 0xFFF
            if d & 0x800:
                d -= 0x1000
            t = (BASE + off + 4 + d * 2) & 0xFFFFFFFF
        elif top == 0x8:
            d = w & 0xFF
            if d & 0x80:
                d -= 0x100
            t = (BASE + off + 4 + d * 2) & 0xFFFFFFFF
        if t == target:
            out.append(BASE + off)
    return out


def gbr_accesses():
    """Full-image scan for any op mentioning gbr (capstone text).

    Returns list of (pc, mnemonic, op_str). Slow one-time scan (~minutes);
    callers should cache. ldc values need pool-chase (see chase_reg).
    """
    out = []
    if CS is None:
        return out
    for off in range(0, len(IMG) - 1, 2):
        try:
            ins = list(CS.disasm(IMG[off:off + 2], BASE + off))
        except Exception:
            continue
        if ins and "gbr" in (ins[0].mnemonic + " " + ins[0].op_str):
            out.append((BASE + off, ins[0].mnemonic, ins[0].op_str))
    return out


def chase_reg(pc, reg, back=40):
    """Look back <=back instructions for mov.l @(disp,PC),Rreg (constant).

    Returns the pool value or None. Straight-line only (stops at branches).
    """
    off = pc - BASE
    for _ in range(back):
        off -= 2
        if off < 0:
            return None
        w = u16(off)
        if w is None:
            return None
        top = w >> 12
        if top in (0x8, 0xA, 0xB) or w == 0x000B or (w & 0xF0FF) in (0x400B, 0x402B):
            return None  # control flow: give up
        if top == 0xD and ((w >> 8) & 0xF) == reg:
            pa = ((((BASE + off) + 4) & ~3) + (w & 0xFF) * 4)
            po = pa - BASE
            if 0 <= po < len(IMG) - 4:
                return struct.unpack_from("<I", IMG, po)[0]
    return None


def main():
    if len(sys.argv) < 3:
        print("usage: shxray.py {func|callers|refs|bsrs} ADDR | gbr | chase ADDR REG")
        return 1
    cmd, addr = sys.argv[1], int(sys.argv[2], 0)
    if cmd == "func":
        print(func_at(addr))
    elif cmd == "callers":
        pools = find_pools(addr)
        print(f"pools holding {addr:#x}:", [hex(p) for p in pools])
        for p in pools[:10]:
            for u in find_pool_users(p)[:10]:
                print(f"  user {u:#x}")
    elif cmd == "refs":
        pools = find_pools(addr)
        print(f"pools holding {addr:#x}:", [hex(p) for p in pools][:20])
        for p in pools[:10]:
            for u in find_pool_users(p)[:10]:
                print(f"  user {u:#x}")
    elif cmd == "bsrs":
        for u in find_bsr_to(addr)[:20]:
            print(f"  bsr/bra {u:#x}")
    elif cmd == "gbr":
        for pc, mn, op in gbr_accesses():
            print(f"  {pc:#x}  {mn:<8} {op}")
    elif cmd == "chase":
        reg = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0
        print(hex(chase_reg(addr, reg) or 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
