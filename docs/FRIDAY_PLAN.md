# Friday release plan (written 2026-10-13 night, updated as work goes)

Plain-English version first, then the technical checklist.

## The honest picture

Ready to ship (proven on the real deck):
- 3-band and RGB waveforms (bottom + centre). User: "perfect".
- PERFORM screen tabs (HOT CUE / BEAT JUMP / KEY SHIFT / STEMS), no crash.
- Live stems mute (works, but ~30 s delay before you hear it).
- Key change using pre-made "key versions" of a track (works, tempo stays
  the same, same ~30 s delay).
- Themes switch (some buttons only half-coloured).

Not ready:
- REAL-TIME key shift (any track, instant). This is research. It needs a
  small change to the deck's audio-chip program. Chance of having it
  working AND deck-tested by Friday: low (maybe 1 in 4). It should NOT
  block the release: ship it later as an update.
- The ~30 s delay on stems/key versions. FIXED IN CODE 2026-10-15 (re-mix
  engine in tools/stems/sm.c, native-proven bit-exact, no installer
  change) -- needs the deck listening test to confirm real latency.
- FLAC/Opus playback can't be installed together with stems yet (small
  code fix, ~half a day).

## Recommendation

Release Friday with what is proven, labelled honestly:
- "Hardware proven": waveforms, PERFORM tabs, stems (with delay note), key
  versions (with delay note), themes (beta).
- "Coming next": real-time key shift, instant stems.

## Order of work until Friday (if limits run out, the next session does this)

1. (Wed, no deck) Fix FLAC+stems chaining (CLAUDE.md §94 problem 2),
   emu test, so the full install includes FLAC/Opus.
2. (Wed, no deck) Rebuild app + tester pack and run the gates:
   `sh tools/build_studio.sh`, `sh tools/make_noob_pack.sh`,
   `.venv/bin/python tools/tester_pack_test.py` (must PASS);
   `PUBLIC=1 sh tools/build_studio.sh`, `tools/public_app_test.py`
   (public snapshot needs rebuilding first, CLAUDE.md §85).
3. (Wed/Thu, deck) One deck session: full install with FLAC, test FLAC +
   Opus, beat jump 32/64 swipe, themes. Update site/README status labels
   to only what the user confirmed.
4. (Thu, no deck) Stems/key-version delay: find the deck's ~30 s
   read-ahead buffer and re-mix it in RAM on a change. If not done by
   Thursday night, ship with the delay note.
5. (Fri) User creates the GitHub org/repo. Then
   `tools/make_public_repo.sh DEST`, user approves the push, Pages, and the
   Release upload. NEVER push this private repo (its history has firmware).
6. Real-time key shift continues in parallel only if time allows; see the
   section below for where it stands.

## Real-time key shift: where it stands

See the bottom of this file (kept up to date by the agent).

### Key shift status (2026-10-13 ~22:00, agent)

BIG STEP: real-time key shift now works inside Pioneer's own DSP program
in the emulator.
- DSP code: tools/keyshift_dsp/ks.c (1,984 B, runs at DSP 0xC005F4C0 =
  loader padding after the program; never used by the deck). Wraps the
  Master Tempo stretcher's input: resample by k, stretch at speed/k.
- tools/keyshift_dsp_emu.py: +2 st -> measured +2.00, tempo x1.000;
  -3 st -> -3.00, x1.000. RESULT: PASS both.
- SAFE delivery (user's choice): tools/keyshift_dsp/ksr.c = one-shot SH
  cave that copies the DSP program to 0x097A0000, adds the code, and has
  the deck's own boot routine reload the audio chip from the copy; the
  original stays untouched, borrowed words are put back, it unhooks
  itself. tools/keyshift_reload_emu.py: RESULT: PASS.
- Deck tool written, NOT yet run on hardware: tools/keyshift_dsp_apply.py
  (install with stick OUT, then `--semis K` while playing, MASTER TEMPO ON).
Remaining before a deck test: (1) emulator load+play AFTER the reload
(running), (2) survey 0x097A0000..+0x60000 on the deck (read-only),
(3) deck_session step, (4) PERFORM KEY SHIFT tab drives it (today only
the command line).

### Update (2026-10-13 ~23:00, agent)
- Key shift, full emulator chain PASS: reload the DSP the safe way ->
  load + play Get Lucky -> +2 semitones measured +2.00, tempo x1.001.
- Deck installer passes against the mock deck (install + reload PASS,
  --semis refuses with no track, --revert PASS). It is deck_session step
  5k (opt-in: `--keyshift`; Studio: "Live Key Shift (beta)" tick box). Still NEVER run on the real deck.
- PERFORM > KEY SHIFT: < RESET > now drive the live shift (-6..+6) when
  no key-version file is loaded; the chosen key is re-applied every frame
  (the deck clears it on each track load).
- FLAC/Opus + stems clash FIXED (FLAC chains to the stems engine); all-
  mods emulator play test still PASS.
- Top-left STEMS [-][+] box removed (user request).
- NOT done, needs a decision from you: hot cue paging by SWIPE. Stock hot
  cues fire the moment you touch; to tell a swipe from a tap we'd have to
  wait until you lift your finger (adds delay to every hot cue). Options:
  (a) swipe only on the tab strip, (b) accept the delay, (c) keep re-tap.

### First deck test for key shift (next deck session, ~10 min)
1. Stick OUT. `.venv/bin/python tools/deck_session.py --size 16 --mode 3band --bj-off --keyshift`
   (or only step 5k: survey + `tools/keyshift_dsp_apply.py` until PASS).
2. Insert stick, load a track, MASTER TEMPO ON, play.
3. PERFORM > KEY SHIFT > tap > twice: should sound 2 semitones higher at
   the same tempo, without the audio stopping. Tap RESET: back to normal.
4. If anything sounds wrong: `tools/keyshift_dsp_apply.py --revert`, or
   power-cycle (always returns the deck to stock).
