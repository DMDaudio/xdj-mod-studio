#!/usr/bin/env python3
"""shdis.py START END -- disassemble the decoded XDJ-700 image
(/tmp/dec4.bin, else cache/blk1.bin), resolving mov.l @(disp,pc) pools."""
import os, struct, sys
import capstone
p = os.environ.get("IMG") or ("/tmp/dec4.bin" if os.path.exists("/tmp/dec4.bin") else "cache/blk1.bin")
d = open(p, "rb").read(); B = 0x08000000
md = capstone.Cs(capstone.CS_ARCH_SH, capstone.CS_MODE_SH4 | capstone.CS_MODE_LITTLE_ENDIAN)
s, e = int(sys.argv[1], 16), int(sys.argv[2], 16)
a = s
while a < e:
    ins = list(md.disasm(d[a - B:a - B + 2], a))
    t = f"{ins[0].mnemonic} {ins[0].op_str}" if ins else f".word {struct.unpack_from('<H', d, a - B)[0]:#06x}"
    if ins and ins[0].mnemonic.startswith("mov.l") and "pc" not in t:
        try:
            q = int(ins[0].op_str.split(",")[0], 16)
            t += f"   ; ={struct.unpack_from('<I', d, q - B)[0]:#x}"
        except Exception:
            pass
    print(f"{a:#x}  {struct.unpack_from('<H', d, a - B)[0]:04x}  {t}")
    a += 2
