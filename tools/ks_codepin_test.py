#!/usr/bin/env python3
"""ks_codepin_test.py -- EMULATOR ONLY.

The code-pin test for the key-shift boot wedge (2026-10-18 §117):

  The DSP's early boot polls UHPI_HD[5]/[2] (GPIO_DAT2 0x01E26070 & 0x24)
  -- a 2-bit code the SH drives on cross-wired pins. In the emulator the
  mapping is  DSP bit5 <- SH 0x04050164 bit1 ;  DSP bit2 <- SH 0x04050128
  bit7.  "Code 2" (= SH 0x04050128 bit7 high) sends the DSP into the
  RESUME path (0x118047C0) instead of a fresh flash boot, so it never
  writes FFF4=1 and the boot's stage-5 poll times out -> 0x101.

  Deck-alike: mid-session the resident pipe leaves that SH pin HIGH; the
  cave never resets it, so even a fully masked attempt wedges (exactly
  the SR-proof'd deck failures). The FIX is for the cave to zero the
  code pins itself before holding the DSP in reset.

  EXP1 (warm-sim): drive SH 0x04050128 bit7 high, run the masked cave
                   attempt -> expect a WEDGE (deck reproduced in emu).
  EXP2 (fix-sim) : same, but zero the code bits just before arming (the
                   level persists while masked) -> expect PASS.

Exit 0 = both expectations held. Snapshots: /tmp/ks_codepin_{warm,fix}.pkl
"""
import os
import sys
import time
sys.path[:0] = [".", "tools"]
from emu.machine import Machine          # noqa: E402
from keyshift_reload_emu import syms, B  # noqa: E402

SNAP = "cache/emu_dsp_mounted6.pkl"
POOL_FLUSH = 0x08C92E20
CODE_B2_PORT = 0x04050128               # SH port whose bit7 = DSP code bit2
CODE_B5_PORT = 0x04050164               # SH port whose bit1 = DSP code bit5
RING = 0x0C8440


def attempt(tag, S, blob, base, pre_code_b2, pre_code_b5, zero_before_arm,
            ring_words=None, max_slices=200):
    m = Machine.load(SNAP)
    mem, d = m.mem, m.mem.dev.dspemu
    m.cpu.run(limit=10_000_000)
    for i, b in enumerate(blob):
        mem.w8(base + i, b)
    prev = mem.r32(POOL_FLUSH)
    mem.w32(S["ksr_prev"], prev)
    mem.w32(POOL_FLUSH, base)
    # simulate the deck's warm-pipe pin levels BEFORE the install
    cur2 = mem.r8(CODE_B2_PORT)
    cur5 = mem.r8(CODE_B5_PORT)
    if pre_code_b2:
        mem.w8(CODE_B2_PORT, cur2 | 0x80)
    if pre_code_b5:
        mem.w8(CODE_B5_PORT, cur5 | 0x02)
    sh = d.sh
    if pre_code_b2:
        sh.write(CODE_B2_PORT, 1, sh.read(CODE_B2_PORT, 1) | 0x80)
    if pre_code_b5:
        sh.write(CODE_B5_PORT, 1, sh.read(CODE_B5_PORT, 1) | 0x02)
    if zero_before_arm:
        sh.write(CODE_B2_PORT, 1, sh.read(CODE_B2_PORT, 1) & 0x7F)
        sh.write(CODE_B5_PORT, 1, sh.read(CODE_B5_PORT, 1) & 0xFD)
    if ring_words:
        for k, v in enumerate(ring_words):
            mem.w32(RING + 4 * k, v)
    print(f"[{tag}] SH stub pins pre-arm: "
          f"{CODE_B2_PORT:#x}={d.sh.read(CODE_B2_PORT, 1):#x} "
          f"{CODE_B5_PORT:#x}={d.sh.read(CODE_B5_PORT, 1):#x}", flush=True)
    mem.w32(S["ksr_flag"], 0x4B535231)     # masked, patched
    st = 0
    seen2 = 0
    for i in range(max_slices):
        m.cpu.run(limit=10_000_000)
        st = mem.r32(S["ksr_status"])
        for e in d.log[-40:]:
            if len(e) > 1 and e[1] == "code-seen":
                seen2 = e[2]
        if st:
            break
    hit = mem.r32(S["ksr_dbg"] + 40)
    print(f"[{tag}] DONE st={st:#x} boot_dbg={hit:#x} last_code_seen={seen2} "
          f"[{time.time() - t0_local():.0f}s]", flush=True)
    m.save(f"/tmp/ks_codepin_{tag}.pkl")
    return st, hit


def t0_local():
    import time as _t
    return _t.time() - _T0[0]


_T0 = [time.time()]


def main():
    S = syms()
    blob = open(os.path.join(B, "ksr.bin"), "rb").read()
    base = S["ksr_entry"]

    stA, _ = attempt("EXP1 warm", S, blob, base, True, False, False)
    expA = stA == 0 or (stA >> 8) != 0        # wedge/retry expected
    print(f"[EXP1] expect-wedge: {'OK' if expA else 'WRONG'}", flush=True)

    stB, _ = attempt("EXP2 fix", S, blob, base, True, False, True)
    expB = stB == 1
    print(f"[EXP2] expect-pass : {'OK' if expB else 'WRONG'}", flush=True)

    if expA and expB:
        print("VERDICT: code-pin theory CONFIRMED -- the cave must zero the "
              "boot-code pins (SH 0x04050128 bit7 / 0x04050164 bit1) before "
              "holding the DSP in reset. 2-byte fix.", flush=True)
        return 0
    print("VERDICT: expectations not met -- log both runs and re-dig",
          flush=True)
    return 1


if __name__ == "__main__":
    _T0[0] = time.time()
    sys.exit(main())
