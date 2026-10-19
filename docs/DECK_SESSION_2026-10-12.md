# Deck session — Mon 12 Oct (tonight)

Everything is RAM only: switching the deck off and on puts it back to stock.

## Before plugging the deck in (on the Mac)

1. Put your USB stick in the Mac.
2. Mod Studio → Track Builder → **3-band for all tracks** (adds the centre
   3-band data next to the bottom-strip data; originals kept as `.orig`).
3. Copy the folder `dist/deck-tests` onto the stick (top level is fine):
   - `Radio 84 (FLAC test).flac`
   - `Radio 84 (Opus test).opus`
   - `Radio 84 (STEMS test).wav`  (live stem mutes)
   - `Radio 84 (KEYS 8).wav`      (key versions)
4. Eject the stick. Keep it OUT of the deck until the install says so.
5. Quit rekordbox and any music / MIDI apps.

## Install (one command, ~20 min, I run it)

Deck on, USB cable to the Mac, no stick, no track loaded. I start the
installer. It plays everything in the emulator first, then installs.
When it says "EJECT/insert the stick", do that.

## Tests (load tracks with BROWSE; the test files are under FOLDER)

| # | What | How | Tell me |
|---|------|-----|---------|
| 1 | Beat jump | PERFORM > BEAT JUMP tab, tap a size pad | jumps 16 beats? |
| 2 | Waveforms | BROWSE > MENU > MY SETTINGS > [WAVEFORM]: RGB, then 3-BAND; load a track each time | centre + bottom look right? photo |
| 3 | RGB vs 1000 MK2 | same track on both decks, same zoom | one photo of each |
| 4 | PERFORM pads | PERFORM > BEAT JUMP tab, pads | jumps? |
| 5 | 8 hot cues | a track with D–H cues, PERFORM > tap HOT CUE again | pages A-C / D-F / G-H, pads jump? |
| 6 | Themes | MY SETTINGS > [THEME], push a few times | colours change, LINK button too? |
| 7 | FLAC | load `Radio 84 (FLAC test)` | plays? |
| 8 | Opus | load `Radio 84 (Opus test)` | plays? |
| 9 | Live stems | load `Radio 84 (STEMS test)`, PERFORM > STEMS, tap VOCALS / DRUMS | mutes? how long until you hear it? any dropouts? |
| 10 | Key versions | load `Radio 84 (KEYS 8)`, PERFORM > KEY SHIFT < / > | key changes, tempo stays? |
| 11 | Key shift live (separate, only if time) | MT on, playing; I run one command | pitch moves, tempo stays? |

If anything freezes: switch the deck off and on (that is always safe),
tell me what you did just before.
