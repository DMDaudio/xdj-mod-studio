#!/usr/bin/env python3
"""gatedcue_apply.py -- deck installer sketch for the gated hot cue.

NEVER RUN without explicit user go-ahead + a read-only survey first.
No deck is touched by importing or planning; --check only READS.

Install order (one shell open per step, >=30 s apart, never chained):
  1. --check   read-only: GC0..GEND all zero, hook pool stock, GSEL/MSOFF
     readable.  ABORT unless the survey is clean (deck RAM differs from
     emu: 0x09608000 live data, 0x091BE000 0x55-filled -- always survey).
  2. --install chunked <=120 B/session (resumable): cave bytes, state
     zero, GSEL stays 0 (OFF) until the user arms it via MY SETTINGS.
     Hook pool LAST in its own fresh session, LW atomic (self-test LW
     on a scratch word first -- byte-wise pool writes are UNSAFE, the
     pool is read every panel frame).
  3. Deck test: load a track WITH hot cues, MY SETTINGS [HOT CUE] -> GATE,
     PERFORM screen: paused-start press/hold/release (preview-while-held,
     ends paused at cue); playing-start (keeps playing); OFF (stock).
  4. --revert  restore hook pool to stock (reboot wipes everything anyway).

Gate: refuse unless no track is loaded ([0x0BA3C050]==0), like
rgb_detail_apply.py (touch path idle).  Install with no track, then load.
"""
import sys

sys.path[:0] = [".", "tools"]
import gatedcue as G

TRACKLEN = 0x0BA3C050


def plan_install():
    c = G.code()
    print(f"cave {len(c)} B @ {G.GC0:#x}")
    print(f"hook {G.HOOK_TOUCH:#x}: {G.ORIG_TOUCH:#x} -> {G.GC0:#x} (LW, last)")
    print(f"GSEL {G.GSEL:#x} default 0=OFF (MY SETTINGS [HOT CUE] row)")
    print("survey: BR GC0..GEND must read ALL ZERO on deck before any write")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if mode == "plan":
        plan_install()
    elif mode in ("check", "install", "revert"):
        sys.exit("deck installer sketch: wire to xdj700_usb_shell chunked "
                 "sessions like rgb_overview_apply.py; NEVER auto-run")
    else:
        sys.exit(__doc__)
