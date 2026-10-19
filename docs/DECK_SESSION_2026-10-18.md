# Next deck session (written 2026-10-18)

Everything here is RAM-only: switching the deck off and on puts it back to
stock. Start from a freshly powered-on deck, USB stick OUT, Mac connected.

## 1. Beat jump (the freeze is fixed — 5 min)

Cause found: one instruction in the BEAT JUMP row painter sat in a "delay
slot" where the real chip refuses it (the same bug class that crashed
STEMS on 10-12). Every frozen test round had it, every clean round didn't.
Also fixed: the row now paints in one go (no flickering numbers), the
left-hand tabs come back when the deck redraws its own label, and STEMS has
4 pads (MIX removed).

    .venv/bin/python tools/deck_session.py --mode 3band

Then: PERFORM → tap BEAT JUMP, HOT CUE, KEY SHIFT, STEMS (with and without a
track). Swipe the BEAT JUMP row through the pages. Expect: no freeze, tabs
always visible.

## 2. Live key shift (new route, no chip restart — 5 min)

The old method restarted the audio chip and failed on the deck in every
form. The new one never restarts anything: it reads the chip's memory first
(read-only), then copies the key-shift code in while it keeps running and
switches one jump. Emulator: +2.00 and −3.00 semitones, tempo ×1.000.

Stick OUT, no track loaded:

    .venv/bin/python tools/keyshift_hot_apply.py --probe

Expect `status 6 = probe OK`. This changes NOTHING. If it says status 7,
stop and send the output.

    .venv/bin/python tools/keyshift_hot_apply.py

Expect `status 1 = installed`. The sound must not stop.
Insert the stick, play a track, MASTER TEMPO ON, then PERFORM → KEY SHIFT
`<` / `>` (or `--semis 2` from the Mac). Listen: key moves, tempo stays.
To undo without a power-cycle: `--revert`.

(Or just tick "Live Key Shift" in Mod Studio — same steps.)

## 3. Optional: phase meter (5 min)

    .venv/bin/python tools/deck_session.py --mode 3band --phasemeter

Load a track: two rulers above the centre waveform, grey beats + red
downbeats scrolling past a white line. The orange master row stays dim
(no Pro DJ Link data yet).

## 4. The 30-second delay (measure it — 5 min)
- Live key shift (step 2), MASTER TEMPO ON, tap >: should change almost
  at once (emulator: 0.05 s).
- STEMS file, tap VOCALS: count the seconds until the vocals drop out.
  EXPECT ~30 s STILL: the emulator shows the deck's read-ahead lives past
  the point our re-mix can reach (the 15 Oct fix only re-mixes one 50 ms
  staging block). Next fix = make the deck jump to its own position after
  a tap so it re-reads the new mix. Same for KEYS version files.
- QUICK TEST of that idea (no new code): tap VOCALS, then straight away
  BEAT JUMP << 1 and >> 1. Do the vocals drop out right after the jump?
  Yes -> the jump flushes the read-ahead and I can automate it.

## 5. Vinyl brake length from the menu (2 min)
BROWSE > MENU > MY SETTINGS > [VINYL BRAKE]: each push = next length
(1X stock ~0.1 s, 2X, 4X, 8X, 15X, 0.5X). Turn VINYL BRAKE on, play,
press PAUSE: the slow-down should get longer each step. [MODS] OFF puts
the stock length back.

## 6. Themes: which buttons get skipped (2 min, read-only)
Push MY SETTINGS > [THEME] once (any theme), then:

    .venv/bin/python tools/theme_skips.py

It lists the artwork blocks the safety check skipped (emulator: 3). Extra
ones are the half-coloured buttons -> exact fix.

## 7. Beat jump 32/64 retest (fix 2026-10-19, needs a fresh install)
A pad could keep the jump it just made: the deck stops reading the touch a
frame early, so the pad's memory was sometimes left set and the NEXT tap
redid the old jump instead of the new one (= "sometimes nothing"). New
taps now always clear it. The 64 pads also only fire on release now, like
the others, so a swipe starting on a 64 pad no longer jumps.
Power-cycle, `deck_session.py --mode 3band --keyshift`, play a track in
the middle, QUANTIZE OFF, tap «32 »32 «64 »64 several times quickly.
(With QUANTIZE ON a jump waits for the beat -- that delay is stock.)
