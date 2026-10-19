# Tester tasks (do in order, report back each one)

Setup each time: deck on, USB to Mac, quit music/MIDI apps, no USB stick
in the deck unless told. All changes vanish on power-cycle. Stop + tell
us at anything unexpected.

> KNOWN BUG 2026-10-14: tapping the new PERFORM-screen pads/tabs can
> freeze the deck (screen stops, power-cycle fixes it, nothing is
> damaged). OPENING the PERFORM screen to look is fine. Until we say
> otherwise: do NOT tap BEAT JUMP / HOT CUE / KEY SHIFT / STEMS pads on
> the deck. Task 2 below is PAUSED. Everything else (waveforms, themes,
> MY SETTINGS rows, FLAC/Opus, key versions via MY SETTINGS) is fair game.

## 0. Report your baseline
Deck serial + firmware version (Utility screen), Mac model. Photo of the
idle screen (optional but helpful).

## 1. Studio + emulator (no deck needed)
- `python -m studio.app`, open the Emulator tab, press Start. Deck screen
  appears? Browse opens? Report anything odd.

## 2. Beat jump pads (deck) -- PAUSED until the freeze bug is fixed
- (Was: full installer + PERFORM > BEAT JUMP pads. Do not run this task
  yet -- the PERFORM taps are what freezes. Wait for our go-ahead.)

## 2b. Safe deck mods (no PERFORM taps)
- Run the full installer: `python tools/deck_session.py --mode 3band`
  (~20 min, it tells you when to pull/reinsert the stick).
- You may OPEN the PERFORM screen to look at it. Do NOT tap its tabs/pads.
- Fair game: BROWSE > MENU > MY SETTINGS > [WAVEFORM] RGB/3-BAND (load a
  track each time, photo please), [THEME] pushes, FLAC + Opus test files.
  (There is no [KEY/STEMS] or [BEAT JUMP] settings row any more -- those
  live on the PERFORM tabs, still paused).

## 3. Theme + waveform preview (Studio only)
- Themes tab: pick hot-pink, move Waveform Blue/RGB/3Band/Custom.
  Preview looks right? Custom colour picker works?

## 4. Key shift demo (Studio only)
- Tools tab: pick any track. If it has no key file, use the Live shift
  slider (−4…+4). Does it play shifted, tempo steady?

## 5. (OPTIONAL, only with us live in chat) RGB overview on deck
- 15–20 min guided install. Say when, we walk through it live.

## 6. Key-shift keeper hook (deck, only with us live in chat)
- Play any track, press PLAY, press MASTER TEMPO (light on).
- Run: `python tools/keyshift_hook_apply.py --semis 2`
  (one USB session, verifies every byte, refuses on mismatch).
- LISTEN right away: pitch up ~2 semitones, tempo steady? Report.
- If the pitch went DOWN instead, run with `--inverse` and report.
- Change key without reinstalling: `--semis -2` (or any -8..+8).
- Stock again: `python tools/keyshift_hook_apply.py --revert`
  (or just power-cycle).

## Feedback per task
What you did, what you saw, deck model/firmware, one photo or screenshot
where it helps. There are no wrong answers — "nothing happened" is data.
