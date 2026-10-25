# XDJ Mod Studio v0.9 (XDJ-700, firmware 1.15)

Open-source mods for the Pioneer DJ XDJ-700, loaded into the deck's memory
over USB. Nothing is flashed: **switch the deck off and on and it is stock
again.** You need your own official XDJ-700 v1.15 update file (from Pioneer
DJ's support site); the app checks it and builds everything from it.

Apple Silicon Mac, macOS 14 or newer (Windows build: see the README inside
the Windows zip). First launch: right-click the app → Open.

## What changed in 0.9.8
- **Phase meter on the PERFORM screen too.** It used to show only on the
  main screen; on PERFORM you got the stock 4-beat boxes. The same meter
  now draws there (same spot as the stock boxes), checked in the emulator
  against the beat grid tick by tick.
- **Mod settings in UTILITY.** Hold MENU and scroll to the end of the
  deck's own UTILITY list: MODS, WAVEFORM COLOR, VINYL BRAKE LENGTH, HOT
  CUE GATE and THEME, changed like any stock setting (push, turn, push).
  UTILITY scrolls, so every row is there on every deck — the MY SETTINGS
  popup only holds 7 rows, and with Pro DJ Link the deck adds its own
  [BACKGROUND COLOR] row there, which is why the Hot Cue Gate row went
  missing on a linked player. The MY SETTINGS rows stay too (on a linked
  deck they now leave out [THEME] instead of [HOT CUE]); both menus change
  the same settings. (A permanent UPD file still carries only the MY
  SETTINGS rows; the UTILITY rows come with the USB install.)
- **BEAT JUMP pads let go properly.** After a tap the deck's touch handler
  sometimes stopped before our pad released its button signal, so the
  jump "button" stayed held until the next touch (found in the emulator).
  It's now released a few frames after your finger leaves the screen, which
  matters with SLIP on.
- **Emulator:** faster audio-chip emulation, cleaner sound when the
  emulator can't keep up (short clean bursts instead of the machine-gun
  stutter), and sound on Windows.

## What changed in 0.9.6
- **Windows gets everything the Mac had.** The 0.9.5 Windows app was built
  from a stale snapshot of the source: the permanent-file builder with its
  extras, the phase meter module, the PERFORM-pad font fix and several
  deck-side fixes never reached it. The Windows reports of "UPD creation
  stops after 2 minutes", "centre colour module not built" and "PERFORM
  pads install failed (AssertionError 2802)" all trace back to that —
  0.9.6 ships one source for both platforms.
- **Key shift freeze fixed** (tester: MT off → key shift → MT on froze the
  deck): the KEY SHIFT tab is inert while MASTER TEMPO is off, and any
  MASTER TEMPO change resets the key to 0.
- **8 hot cues: tap works too.** Paging was swipe-only; three testers
  naturally tapped the HOT CUE tab. Tap and swipe both page A–C / D–F / G–H.
- **Phase meter: reads the time digits' own position source.** The word it
  used first freezes on a real deck (the beats never scrolled); it now
  reads the live one, verified in the emulator against a tick-by-tick model.
- **FLAC & Opus can be left out of an install** — it was 10 of the 15–20
  minutes. Untick it on the Mods page and add it later.
- **Permanent (UPD) file presets** — "Phase meter only" and "Waveform
  colours only" one-click presets next to the tick boxes.
- If colours or the phase meter look gone after a trip through MY SETTINGS:
  check [WAVEFORM] is not BLUE and [MODS] is ON in that menu — those rows
  switch them off.

## Tested on a real deck
- **RGB and 3-band waveforms** — the overview strip and the big centre
  waveform, from the colour data rekordbox already exports.
- **Live key shift** — PERFORM › KEY SHIFT moves the key up or down while
  the track plays (MASTER TEMPO on); tempo stays put and the panel shows
  the track's real key.
- **Live themes** — step through colour themes from MY SETTINGS; OFF
  brings back the exact stock look.
- **Live stems** — mute vocals / drums / bass / other from PERFORM ›
  STEMS. A mute is heard after about 30 seconds.
- **Beat jump pads** — PERFORM › BEAT JUMP: tap to jump, swipe the row for
  1 up to 64 beats.
- **The permanent (UPD) file** — one tester flashed it on a real player:
  version 1.16 ran with the RGB overview, beat jump, key shift, stems and
  hot cue paging after power-off.

## Working in the emulator, not yet tested on a deck
These run on the deck's own firmware in the emulator, so they should work
on a real deck too, but nobody has tried them on one yet:
- **Faster stem mute (beta)** — rewrites the audio the deck has already
  buffered, so a mute is heard in under a second in the emulator.
- **Phase meter with the live position source** — this deck's beats and
  downbeats above the waveform, main screen and PERFORM (the 0.9.5 build's beats stood still on a
  deck; this one reads the clock's own word).
- **Vinyl brake length** — MY SETTINGS › [VINYL BRAKE], 0.5× to 15×.
- **8 hot cues** — A–H, in pages of three (swipe or tap the tab).
- **FLAC and Opus** playback.

## Known issues
- **Live key shift (beta, off by default):** one tester's deck freezes
  with "file corrupted" when the key changes; it works on our deck and in
  the emulator (MP3 and WAV, ±6 semitones), so we're still hunting the
  difference. Leave it unticked if you play live.
- The emulator's audio stutters (the emulated audio chip runs slower than
  real time). Browsing, loading and the screens are fine.
- Stems need PWV6/PWV7 waveform data in the track's .EXT (rekordbox's
  Track Builder can add it); without it the STEMS tab shows the mix only.

## Safety
Everything lives in the deck's memory; a power-cycle always returns it to
stock. The optional **Permanent (UPD)** tab builds a firmware file you
would flash yourself. That one *can* brick a deck, and the app says so
before it builds anything.

## Open source
All of it — the Mac app, the emulator, every mod and the tools that build
them — is on GitHub under the MIT licence. Pioneer DJ's firmware is not
included; you add your own copy.

Not affiliated with Pioneer DJ / AlphaTheta.
