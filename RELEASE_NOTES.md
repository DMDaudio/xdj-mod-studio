# XDJ Mod Studio — public beta (XDJ-700, firmware 1.15)

Mods for the Pioneer DJ XDJ-700, loaded into the deck's memory over USB.
Nothing is flashed: **switch the deck off and on and it is stock again.**
You need your own official XDJ-700 v1.15 update file (from Pioneer DJ's
support site); the app checks it and builds everything from it.

Apple Silicon Mac, macOS 14 or newer. First launch: right-click the app → Open.

## Tested on a real deck
- **RGB and 3-band waveforms** — the bottom overview and the big centre
  waveform. Uses the colour data rekordbox already exports.
- **MY SETTINGS rows** (BROWSE › MENU › MY SETTINGS) — waveform style,
  mods on/off, theme, and more.
- **PERFORM pads** — new BEAT JUMP, HOT CUE, KEY SHIFT and STEMS tabs on
  the touch screen. Swipe the BEAT JUMP row for bigger jumps (up to 64).
- **Live themes** — step through your saved colour themes from MY
  SETTINGS; OFF restores the exact stock look.
- **Key and stem versions** — one file holds the original plus key-shifted
  or stem versions (made in Mod Studio); switch between them on the deck.
- **Live stem mutes** — mute vocals / drums / bass / other on STEMS files.
  Works on the deck with a delay of about 30 seconds.
- **Live key shift** — PERFORM › KEY SHIFT shifts any track's key live,
  tempo unchanged (MASTER TEMPO on). Shows the track's real key.

## Beta (opt-in tick boxes, still settling)
- **Vinyl brake length** — MY SETTINGS › [VINYL BRAKE], 0.5× to 15×.
- **Phase meter** — beat rulers above the centre waveform (this deck only;
  the master row needs Pro DJ Link support).
- **8 hot cue pages** — A–H, swiped in pages of three.
- **FLAC and Opus** — play like WAV files.

## Known issues
- Key/stem *version files* still switch with a ~30 s delay (use live key
  shift instead: it's instant).
- **Faster stem mute (beta):** rewrites the audio the deck has already
  buffered, so a mute is heard in under a second in the emulator (on the
  deck: by the next read burst, a few seconds at most). Not yet tested on a
  real deck.
- Themes recolour the deck's own artwork; the new PERFORM pads keep their
  own blue for now.
- The emulator is a beta: its audio stutters (the emulated audio chip runs
  slower than real time). Browsing, loading and the screens are fine.

## Safety
Everything lives in the deck's memory; a power-cycle always returns it to
stock. The optional **Permanent (UPD)** tab builds a firmware file you
would flash yourself — that one *can* brick a deck, has never been tested
on hardware, and is clearly marked as such in the app.

Not affiliated with Pioneer DJ or AlphaTheta.
