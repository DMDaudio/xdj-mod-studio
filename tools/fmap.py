"""fmap.py -- code / literal-pool map for a decompressed SH-4 image.

WHY
Several confident-but-wrong findings in this project traced to one cause: with
linear disassembly you cannot tell an instruction from a literal-pool word.
Pool bytes decode as plausible instructions, and pool *addresses* look like
code, so "is this a constant?" and "is this an instruction?" both got answered
by coincidence. See the corrections in THEME_ARCHITECTURE.md.

METHOD
Break the circularity by starting from things that are definitely code and
following control flow:

  1. Seed from pointers that look like code addresses (0x08xxxxxx into the
     image) whose target decodes as a real prologue.
  2. Sweep each seed forward, decoding instructions, until an unconditional
     end (rts / jmp / bra, plus its delay slot) with no pending forward branch.
  3. Every `mov.l @(disp,PC)` / `mov.w @(disp,PC)` encountered marks its TARGET
     as pool. Pool words are then known data, never instructions.
  4. Bytes covered by a sweep are code; bytes marked pool are data.

A byte can end up in neither, which is honest: it means unproven.
"""
import struct, collections

CBASE, BASE = 0x08000000, 0xA8000000


def sweep(d, start, limit=0x4000):
    """Decode forward from `start`. -> (code_end, pool_targets, ok)"""
    pool = set()
    p = start
    end = min(len(d) - 2, start + limit)
    last_end = None
    maxbranch = start
    while p < end:
        w = struct.unpack_from('<H', d, p)[0]
        if p in pool:                       # ran into a literal pool
            return (last_end or p), pool, last_end is not None
        top = w >> 12
        if top == 0xD:                      # mov.l @(disp,PC),Rn
            pool.add(((p + 4) & ~3) + (w & 0xFF) * 4)
        elif top == 0x9:                    # mov.w @(disp,PC),Rn
            pool.add(p + 4 + (w & 0xFF) * 2)
        elif top == 0xC and ((w >> 8) & 0xF) == 7:   # mova
            pool.add(((p + 4) & ~3) + (w & 0xFF) * 4)
        # branch targets keep the function alive past an rts
        if top == 0xA or top == 0xB:        # bra / bsr
            disp = w & 0xFFF
            if disp & 0x800:
                disp -= 0x1000
            t = p + 4 + disp * 2
            if top == 0xA and t > maxbranch:
                maxbranch = t
        elif top == 0x8 and ((w >> 8) & 0xF) in (0x9, 0xB, 0xD, 0xF):
            dd = w & 0xFF
            if dd & 0x80:
                dd -= 0x100
            t = p + 4 + dd * 2
            if t > maxbranch:
                maxbranch = t
        # unconditional terminators
        term = (w == 0x000B or w == 0x002B or                # rts, rte
                (w & 0xF0FF) == 0x402B or                    # jmp @Rn
                top == 0xA)                                  # bra
        if term:
            last_end = p + 4                                 # include delay slot
            if maxbranch <= p:
                return last_end, pool, True
        p += 2
    return (last_end or p), pool, last_end is not None


def call_targets(d, lo, hi, pool):
    """bsr / jsr-via-pool targets inside a swept range -> more function entries."""
    out = set()
    for p in range(lo, min(hi, len(d) - 2), 2):
        if p < len(pool) and pool[p]:
            continue
        w = struct.unpack_from('<H', d, p)[0]
        if (w >> 12) == 0xB:                       # bsr
            disp = w & 0xFFF
            if disp & 0x800:
                disp -= 0x1000
            out.add(p + 4 + disp * 2)
    return out


def build(d, seeds, verbose=False):
    code = bytearray(len(d))      # 1 = swept as code
    pool = bytearray(len(d))      # 1 = literal-pool word
    done = set()
    for s in sorted(seeds):
        if s in done or s >= len(d) - 2:
            continue
        e, pl, ok = sweep(d, s)
        if not ok:
            continue
        done.add(s)
        for i in range(s, min(e, len(d))):
            code[i] = 1
        for t in pl:
            if 0 <= t < len(d) - 3:
                for i in range(t, t + 4):
                    pool[i] = 1
    # a pool word is never code
    for i in range(len(d)):
        if pool[i]:
            code[i] = 0
    return code, pool


def build_iter(d, seeds, rounds=6, verbose=True):
    """Sweep, harvest call targets from swept code, repeat to a fixed point."""
    code = bytearray(len(d)); pool = bytearray(len(d))
    frontier = set(seeds); done = set()
    for r in range(rounds):
        newseeds = set()
        added = 0
        for s in sorted(frontier):
            if s in done or not (0 <= s < len(d) - 2):
                continue
            e, pl, ok = sweep(d, s)
            if not ok:
                continue
            done.add(s); added += 1
            for i in range(s, min(e, len(d))):
                code[i] = 1
            for t in pl:
                if 0 <= t < len(d) - 3:
                    for i in range(t, t + 4):
                        pool[i] = 1
            newseeds |= call_targets(d, s, e, pool)
            # jsr via pool: any pool word that is a code pointer
            for t in pl:
                if 0 <= t < len(d) - 3:
                    v = struct.unpack_from('<I', d, t)[0]
                    if CBASE <= v < CBASE + len(d):
                        a = v - CBASE
                        if a % 2 == 0:
                            newseeds.add(a)
        if verbose:
            print(f"   round {r}: swept {added:,} functions, "
                  f"{len(newseeds - done):,} new seeds")
        frontier = newseeds - done
        if not frontier:
            break
    for i in range(len(d)):
        if pool[i]:
            code[i] = 0
    return code, pool, done


def is_prologue(d, a):
    if a + 4 > len(d):
        return False
    w = struct.unpack_from('<H', d, a)[0]
    return w == 0x4F22 or (w & 0xFF0F) == 0x2F06 or (w & 0xFF00) == 0x7F00


def seeds_from_pointers(d):
    """Code pointers (0x08xxxxxx) whose target looks like a function entry."""
    import numpy as np
    W = np.frombuffer(d[:len(d) // 4 * 4], dtype='<u4')
    cand = W[(W >= CBASE) & (W < CBASE + len(d))]
    out = set()
    for v in set(int(x) for x in cand):
        a = v - CBASE
        if a % 2 == 0 and is_prologue(d, a):
            out.add(a)
    return out
