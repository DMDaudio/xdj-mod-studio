#!/usr/bin/env python3
"""keyshift_dsp_apply.py [--semis K] [--revert] -- REAL-TIME KEY SHIFT on
the DECK. RAM only; a power-cycle always returns the deck to stock.
NOT YET RUN ON HARDWARE (emulator: tools/keyshift_reload_emu.py PASS,
tools/keyshift_dsp_emu.py PASS +2.00 st / tempo x1.000).

How it works (CLAUDE.md §95):
  * build/keyshift/ksr.bin = a one-shot SH cave (0x09800000) carrying the
    DSP key-shift code. Armed on the display-flush pool; on the next frame
    it copies the DSP program to 0x097A0000 (the ORIGINAL in RAM is never
    changed), appends the key-shift code, re-points one call in the copy,
    has the deck's own DSP boot routine reload the audio chip from the
    copy, puts every borrowed word back and unhooks itself.
    Only with NO TRACK LOADED (stick out) -- the audio chip restarts.
  * live control: the SH store at 0x08D36B3A (tempo copy -> mailbox BD8)
    is NOPed with one atomic WW; 0x09D24DD8 then carries our word:
    0x40000000|Q20(2^(K/12)) = shift K semitones, 0x00100000 = off.
    Works with MASTER TEMPO ON (that path is where the shift lives).

Usage:
  keyshift_dsp_apply.py              install + reload (re-run until PASS:
                                     chunked write, then arm in a short
                                     session); needs the survey of
                                     0x097A0000..+0x60000 remembered for
                                     this deck (deck_session step 5k)
  keyshift_dsp_apply.py --semis 2    shift +2 semitones (LW, instant; a
                                     track must be loaded; a new track load
                                     clears it -- the PERFORM KEY SHIFT
                                     buttons keep it instead)
  keyshift_dsp_apply.py --semis 0    no shift
  keyshift_dsp_apply.py --revert     shift off, BD8 store back to stock
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, "tools")
OUT = "build/keyshift"
HOOK = 0x08C92E20           # display-flush pool (read every frame)
TRACK_LEN = 0x0BA3C050
POOL_LEN, LEN_STOCK = 0x08D78AC8, 0x0005F4C0
POOL_SRC, SRC_STOCK = 0x08C37918, 0xA800E770
CALL_SH, CALL_STOCK = 0x080174D4, 0x0027D290
NOP_SITE, NOP_STOCK, NOP = 0x08D36B3A, 0x1C15, 0x0009
KEY_WORD = 0x09D24DD8
DST, DST_LEN = 0x097A0000, 0x60000
OFF = 0x00100000            # untagged Q20 1.0 = stock DSP behaviour


def syms():
    s = {}
    for line in open(f"{OUT}/ksr.syms"):
        a, _, n = line.split()
        s[n.lstrip("_")] = int(a, 16)
    return s


def key_word(semis):
    if semis == 0:
        return OFF
    return 0x40000000 | int(round(2.0 ** (semis / 12.0) * (1 << 20)))


def survey_ranges():
    return [(a, min(0x10000, DST + DST_LEN - a)) for a in range(DST, DST + DST_LEN, 0x10000)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--semis", type=float)
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--stocktest", action="store_true",
                    help="arm in STOCK mode ('KSR0'): reload the audio chip "
                         "from an unpatched program copy (ksr.c v3). Splits "
                         "environment vs patch after the 0x101: sound should "
                         "come back by itself on success; stops before the "
                         "key-control step")
    ap.add_argument("--nomask", action="store_true",
                    help="run the boot call UNMASKED (flag bit 0x100; ksr.c "
                         "v6 lets the deck's own pipe service run)")
    ap.add_argument("--longhold", action="store_true",
                    help="2-second reset hold before the boot (flag bit "
                         "0x200; was ~4 ms)")
    ap.add_argument("--forcearm", action="store_true",
                    help="arm even with a previous failed attempt's status "
                         "(for the flag-bit experiment ladder)")
    a = ap.parse_args()
    req_flag = 0x4B535230 if a.stocktest else 0x4B535231   # 'KSR0' / 'KSR1'
    if a.nomask:
        req_flag |= 0x100                                  # v6: unmasked
    if a.longhold:
        req_flag |= 0x200                                  # v6: 2 s hold
    if a.semis is not None and not -6 <= a.semis <= 6:
        sys.exit("semis range is -6..+6")
    s = syms()
    base = s["ksr_entry"]
    img = open(f"{OUT}/ksr.bin", "rb").read()

    import hid
    import fastload
    from xdj700_usb_shell import PIONEER_VID, open_deck, drain
    from rgb_overview_apply import br, read_block, send
    from shellbyte import cmd, read_byte

    def rd32(h, addr):
        return struct.unpack("<I", bytes(br(h, addr, 4)))[0]

    def rd16(h, addr):
        # 2026-10-17 night: single-value 0BR reads answer E06 on the real
        # shell (never validated before; every proven read path uses
        # count >= 2). Read the word as one 2-byte BR instead.
        vals = br(h, addr, 2)
        return vals[0] | (vals[1] << 8)

    def lw(h, addr, val):
        q = f"{addr:x},{val:x}LW".encode() + b"\r\x00"
        h.write(q + b"\x00" * (26 - len(q)))
        drain(h, 0.5)
        if rd32(h, addr) != val:
            return False
        return True

    def ww(h, addr, v):
        cmd(h, f"{addr:x},{v:x}WW\r\x00".encode(), f"WW {addr:#x}", 2.0)
        return rd16(h, addr) == v

    cands = [d for d in hid.enumerate()
             if d["vendor_id"] == PIONEER_VID and d["usage"] == 0x01]
    if len(cands) != 1:
        sys.exit(f"found {len(cands)} shell interface(s)")
    h = open_deck(PIONEER_VID, cands[0]["product_id"], 0x01)
    try:
        drain(h, 1.0)
        send(h, "$CDJ", 1.5)
        serial = str(getattr(h, "serial", "") or "unknown")
        status = rd32(h, s["ksr_status"])
        site = rd16(h, NOP_SITE)

        if a.revert or a.semis is not None:
            if a.revert:
                if site == NOP:
                    lw(h, KEY_WORD, OFF)        # best effort; the stock store
                    if not ww(h, NOP_SITE, NOP_STOCK):
                        sys.exit("FAIL: store restore - power-cycle the deck")
                print("RESULT: PASS - key shift off, BD8 store stock "
                      "(audio chip keeps the inactive code until power-off)")
                return 0
            if status != 1 or site != NOP:
                sys.exit(f"REFUSED: key shift not installed on this boot "
                         f"(status {status:#x}, store {site:04x}) - run without "
                         f"--semis first")
            w = key_word(a.semis)
            if rd32(h, TRACK_LEN) == 0:
                sys.exit("REFUSED: load and play a track first (the deck clears "
                         "the key word while no track is loaded)")
            if not lw(h, KEY_WORD, w):
                sys.exit("FAIL: key word did not verify - try again while playing")
            print(f"RESULT: PASS - key {a.semis:+g} semitones (word {w:#010x}); "
                  f"needs MASTER TEMPO ON")
            return 0

        # ---- install + reload
        if status == 1 and site == NOP:
            print("RESULT: PASS (already installed on this boot)")
            return 0
        if rd32(h, TRACK_LEN) != 0:
            sys.exit("REFUSED: a track is loaded (track length nonzero) - eject "
                     "the USB stick first; the audio chip restarts")
        live = rd32(h, HOOK)
        stock = (rd32(h, POOL_LEN) == LEN_STOCK and rd32(h, POOL_SRC) == SRC_STOCK
                 and rd32(h, CALL_SH) == CALL_STOCK and site in (NOP_STOCK, NOP))
        if not stock:
            sys.exit("FAIL: DSP boot words / BD8 store not stock - nothing written")
        if status not in (0, 1):
            if a.forcearm:
                print(f"NOTE: arming with the previous status {status:#x} "
                      "(--forcearm)")
            else:
                sys.exit(f"FAIL: an earlier reload attempt reported "
                         f"{status:#x} - power-cycle the deck before trying "
                         f"again")
        if status == 0:
            unknown = [f"{r:#x}:{n:#x}" for r, n in survey_ranges()
                       if not fastload._known_zero(serial, r, n)]
            if unknown:
                sys.exit("REFUSED: survey the copy area first (deck_session "
                         "step 5k or deck_zero_survey.py " +
                         " ".join(x.replace("0x", "") for x in unknown) + ")")
            if live == base:
                print("armed earlier, waiting for the reload ...")
            else:
                want = bytearray(img)
                po = s["ksr_prev"] - base
                want[po:po + 4] = struct.pack("<I", live)
                for k in ("ksr_flag", "ksr_status"):
                    o = s[k] - base
                    want[o:o + 4] = b"\0\0\0\0"
                cur = read_block(h, base, len(want))
                # skip our own runtime words: a previous attempt on this
                # boot may have left status/dbg values there (all ours)
                skip = list(range(po, po + 4))
                for k in ("ksr_flag", "ksr_status"):
                    o = s[k] - base
                    skip += list(range(o, o + 4))
                o = s["ksr_dbg"] - base
                skip += list(range(o, o + 88))
                bad = [i for i, (c, w) in enumerate(zip(cur, want))
                       if c and c != w and i not in skip]
                if bad:
                    sys.exit(f"FAIL: cave +{bad[0]:#x} holds foreign data - nothing armed")
                if cur != bytes(want):
                    left = fastload.install_region(h, base, bytes(want),
                                                   fastload.SESSION_BUDGET,
                                                   allow_live=range(base + po, base + po + 4),
                                                   cur=cur)
                    if left:
                        print(f"RESULT: PARTIAL ({left} B left) - run again")
                        return 0
                    if fastload.arm_later() and not os.environ.get("KSA_NOARM"):
                        print("code complete - run again to arm (fresh short session)")
                        return 0
                if os.environ.get("KSA_NOARM"):
                    # the code is complete on BOTH paths here (fresh boot:
                    # cur == want; else install_region just finished it)
                    print("code complete (KSA_NOARM) - run again to arm")
                    return 0
                if not lw(h, s["ksr_status"], 0x12345678) or not lw(h, s["ksr_status"], 0):
                    sys.exit("FAIL: LW self-test - nothing armed (deck unchanged)")
                if not lw(h, HOOK, base):
                    lw(h, HOOK, live)
                    sys.exit("FAIL: hook verify - restored; power-cycle before playing")
            if rd32(h, TRACK_LEN) != 0:
                lw(h, HOOK, live)
                sys.exit("REFUSED: a track got loaded - hook removed, nothing reloaded")
            if not lw(h, s["ksr_flag"], req_flag):
                # the cave consumes the flag on its first screen frame, so
                # this verify can legitimately lose the race (seen live
                # 2026-10-17 with the reload SUCCEEDING). Also: the deck's
                # USB link goes down while the reload runs (both attempts
                # died at exactly this point with an I/O timeout), so do
                # NOT poll here -- a traceback here would only hide the
                # result. Read it separately once the deck answers again.
                print("RESULT: PENDING - flag consumed (verify race lost, "
                      "normal): the reload is running and the deck's USB "
                      "goes down for a few minutes; read the result with "
                      "tools/ks_diag_read.py once it answers again")
                return 0
            t = time.time()
            while time.time() - t < 8:
                status = rd32(h, s["ksr_status"])
                if status:
                    break
                time.sleep(0.5)
            if status != 1:
                if status >= 0x100:
                    # diagnostic snapshot (ksr.c v2): plain reads recorded
                    # before/after the failed boot -- safe to fetch here
                    dbg_base = s["ksr_dbg"]
                    raw = read_block(h, dbg_base, 88)
                    dbg = [raw[k] | raw[k + 1] << 8 | raw[k + 2] << 16
                           | raw[k + 3] << 24 for k in range(0, 88, 4)]
                    names = ["HPIC pre", "HPIA pre", "ring0", "ring1",
                             "ring2", "ring3", "ring4", "ring5", "ring6",
                             "ring7", "BOOT ret", "HPIC post", "HPIA post",
                             "ring0p", "ring1p", "ring2p", "ring3p",
                             "ring4p", "ring5p", "ring6p", "ring7p", "pins"]
                    for n, v in zip(names, dbg[:22]):
                        print(f"  dbg {n:10s} {v:#010x}")
                sys.exit(f"FAIL: reload status {status:#x} (2 = track loaded, "
                         f"3 = not stock, 4 = copy check, 0x10x = DSP boot "
                         f"error) - power-cycle the deck")
            if rd32(h, HOOK) != live:
                sys.exit("FAIL: hook still ours after reload - power-cycle the deck")
            print(f"audio chip reloaded ({time.time() - t:.1f} s)")
            if a.stocktest:
                print("RESULT: PASS - STOCK-copy reload worked on this deck: "
                      "the boot environment is healthy, so the PATCH content "
                      "was what the real chip rejected. Sound should be back "
                      "right now without a power-cycle: insert the stick and "
                      "press PLAY to confirm.")
                return 0
        # ---- live control: own the BD8 slot
        # (with no track loaded the deck itself keeps 0x09D24DD8 at 0, and
        # clears it again on every track load: the PERFORM pads re-assert
        # the chosen key every frame, --semis needs a track playing)
        if site != NOP:
            if not ww(h, NOP_SITE, NOP):
                sys.exit("FAIL: BD8 store NOP did not verify - power-cycle the deck")
        print("RESULT: PASS - key shift ready: insert the stick, play a track "
              "with MASTER TEMPO ON, then keyshift_dsp_apply.py --semis 2")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
