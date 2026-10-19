# XDJ Mod Studio v0.9 (XDJ-700, firmware 1.15)

Open-source mods for the Pioneer DJ XDJ-700, loaded into the deck's memory
over USB. Nothing is flashed: **switch the deck off and on and it is stock
again.** You need your own official XDJ-700 v1.15 update file (from Pioneer
DJ's support site); the app checks it and builds everything from it.

Apple Silicon Mac, macOS 14 or newer. First launch: right-click the app → Open.

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

## Working in the emulator, not yet tested on a deck
These run on the deck's own firmware in the emulator, so they should work
on a real deck too, but nobody has tried them on one yet:
- **Faster stem mute (beta)** — rewrites the audio the deck has already
  buffered, so a mute is heard in under a second in the emulator.
- **Phase meter** — this deck's beats and downbeats above the waveform.
- **Vinyl brake length** — MY SETTINGS › [VINYL BRAKE], 0.5× to 15×.
- **8 hot cues** — A–H, in pages of three.
- **FLAC and Opus** playback.

## Known issues
- The emulator's audio stutters (the emulated audio chip runs slower than
  real time). Browsing, loading and the screens are fine.

## Safety
Everything lives in the deck's memory; a power-cycle always returns it to
stock. The optional **Permanent (UPD)** tab builds a firmware file you
would flash yourself. That one *can* brick a deck, has never been tested
on hardware, and the app says so before it builds anything.

## Open source
All of it — the Mac app, the emulator, every mod and the tools that build
them — is on GitHub under the MIT licence. Pioneer DJ's firmware is not
included; you add your own copy.

Not affiliated with Pioneer DJ or AlphaTheta.
