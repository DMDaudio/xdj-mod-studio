#!/usr/bin/env python3
"""sh_taudit.py -- find T-flag clobbers between a compare and its branch.

Real SH-4 (Ghidra SLEIGH superh.sinc, "clear or set T"): shll / shal /
shlr / shar / rotl / rotr WRITE T with the bit shifted out. The 2/8/16-bit
shifts (shll2, shlr8, ...) and shad/shld do NOT touch T. So

    cmp/hs r3, r2
    shll r8          <-- destroys the compare's T
    bf  label        <-- branches on the SHIFT's T, not the compare's

silently computes the wrong thing -- and it looks right in a simulator only
if the simulator has the same (wrong) idea, so it can hide for years.

This scans every shasm kernel in the given modules (or .asm files) and
reports any T-setting instruction followed, before the next T setter, by a
single-bit shift/rotate and then a bf/bt.

Run: .venv/bin/python tools/sh_taudit.py pv_phase resample pv_core_test
     .venv/bin/python tools/sh_taudit.py path/to/kernel.asm
"""
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble

# instructions that WRITE T
T_SET = {"cmp/eq", "cmp/hs", "cmp/ge", "cmp/hi", "cmp/gt", "cmp/pl", "cmp/pz",
         "tst", "tst.b", "dt"}
# instructions that WRITE T only for single-bit shifts/rotates (SH semantics)
T_CLOBBER = {"shll", "shlr", "shar", "shal", "rotl", "rotr", "rotcl", "rotcr"}
# instructions that READ T
T_USE = {"bf", "bt", "bf/s", "bt/s", "movt"}
# real SH-4 sets T (to "result is zero") for these; the py core does not
# (known deviation, CLAUDE.md sec 9) -- so on hardware they also break a
# compare->branch pair. Advisory, not a hard failure.
T_HW_SET = {"and", "or", "xor"}


def decode(src):
    """-> [(offset, mnemonic, op_str)] using capstone, or [] if unavailable."""
    try:
        from capstone import Cs, CS_ARCH_SH, CS_MODE_SH4, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        return None
    md = Cs(CS_ARCH_SH, CS_MODE_SH4 | CS_MODE_LITTLE_ENDIAN)
    return [(i.address, i.mnemonic, i.op_str) for i in md.disasm(assemble(src), 0)]


def audit(name, src):
    ins = decode(src)
    if ins is None:
        print(f"{name}: capstone missing, skipped")
        return [], []
    bad, soft = [], []
    for i, (addr, mn, ops) in enumerate(ins):
        if mn not in T_SET:
            continue
        clobber = None
        for j in range(i + 1, len(ins)):
            a2, m2, o2 = ins[j]
            if m2 in T_SET:
                break                   # a fresh setter supersedes ours
            if m2 in T_CLOBBER:
                clobber = (a2, m2, o2)
                continue
            if m2 in T_USE:
                if clobber is not None:
                    bad.append((addr, mn, ops, clobber, (a2, m2, o2)))
                elif m2 in ("bf", "bt", "bf/s", "bt/s"):
                    hit = [ins[k] for k in range(i + 1, j)
                           if ins[k][1] in T_HW_SET]
                    if hit:
                        soft.append((addr, mn, ops, hit[0]))
                break
            if m2.startswith("b"):
                break                   # bra/bsr: control leaves the block
    return bad, soft


def kernels_from_module(modname):
    import importlib
    import importlib.util
    if modname.endswith(".py"):
        spec = importlib.util.spec_from_file_location(
            "_audit_mod", modname)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        tag = modname.rsplit("/", 1)[-1][:-3]
    else:
        m = importlib.import_module(modname)
        tag = modname
    out = {}
    for k, v in vars(m).items():
        looks_asm = (isinstance(v, str) and re.search(r"\n[a-z_0-9]+:\n", v)
                     and "rts" in v)
        if looks_asm:
            out[f"{tag}.{k}"] = v
    return out


def main(argv):
    mods = [a for a in argv if not a.endswith(".asm")]
    files = [a for a in argv if a.endswith(".asm")]
    todo = {}
    for m in mods:
        todo.update(kernels_from_module(m))
    for f in files:
        todo[f] = open(f).read()
    total = 0
    for name, src in sorted(todo.items()):
        bad, soft = audit(name, src)
        if bad:
            total += len(bad)
            print(f"FAIL {name}")
            for addr, mn, ops, cl, br in bad:
                print(f"     {addr:#06x} {mn} {ops}   ->  {cl[1]} {cl[2]} "
                      f"@{cl[0]:#06x}  ->  {br[1]} @{br[0]:#06x}")
        elif soft:
            total += len(soft)
            print(f"WARN {name}")
            for addr, mn, ops, cl in soft:
                extra = f" (and/or/xor at {cl[0]:#06x})" if cl else ""
                print(f"     {addr:#06x} {mn} {ops} -> branch with no shift "
                      f"between{extra}; fine only if the result is non-zero")
        else:
            print(f"ok   {name}")
    print(f"TA-AUDIT: {total} suspicious T use(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))