# XDJ-700 Mods

Open-source mods for the **XDJ-700** (firmware 1.15): RGB and 3-band
waveforms, live key shift, stems, beat jump pads, live colour themes and
more. Fully open source (MIT). There's also a Mac and Windows app,
**Mod Studio**, which prepares your tracks, designs themes, runs your deck's
firmware in a built-in emulator and installs the mods.

**Nothing is ever flashed.** Mods are loaded into the deck's working memory
over USB through the deck's own service connection. Switch the deck off and
on and it is completely stock again.

> Independent community project, not affiliated with, endorsed by or
> supported by Pioneer DJ / AlphaTheta. "Pioneer DJ", "XDJ", "CDJ" and
> "rekordbox" are trademarks of their owners and are used only to describe
> compatibility. Use at your own risk.

## What works

**Tested on a real deck**

| Mod | Notes |
| --- | --- |
| RGB and 3-band waveforms | overview strip and centre display |
| Live key shift | PERFORM › KEY SHIFT, MASTER TEMPO on, tempo stays put |
| Live themes | from MY SETTINGS; OFF restores the stock colours exactly |
| Live stems | PERFORM › STEMS mutes vocals / drums / bass / other, heard after ~30 s |
| Beat jump pads | PERFORM › BEAT JUMP, tap and swipe (1–64 beats) |
| MY SETTINGS rows | waveform style, mods on/off, theme |

**Working in the emulator, not yet tested on a deck.** These run against the
deck's real firmware in the emulator, so they should work on a real deck,
but nobody has tried them on one yet:

| Mod | Notes |
| --- | --- |
| Faster stem mute (beta) | rewrites the audio the deck already buffered; < 1 s in the emulator |
| Phase meter | this deck's beats; the master row needs link data |
| Vinyl brake length | MY SETTINGS › [VINYL BRAKE], 0.5×–15× |
| 8 hot cues | pages A–C / D–F / G–H |
| FLAC and Ogg Opus playback | own decoders on the deck |

## For DJs: Mod Studio

1. Download Mod Studio from Releases.
   - **Mac** (Apple Silicon, macOS 14+): `XDJ-Mod-Studio.zip`. The first
     time, right-click the app and choose Open.
   - **Windows** (64-bit Windows 10/11, beta): `XDJ-Mod-Studio-Windows.zip`.
     Unzip it and run `XDJ Mod Studio.exe` (SmartScreen: More info → Run
     anyway). Every build is checked by CI on a clean Windows
     machine (the app starts, its pages load, the emulator engine loads).
     Running your firmware in the emulator and installing to a real deck
     from Windows have **not been tried by a person yet** (both are proven
     on a Mac), and the emulator is silent on Windows for now. Please report how it goes.
2. **Add your firmware.** Get the official XDJ-700 firmware 1.15 update file
   from Pioneer DJ's support site and give it to Mod Studio when it asks.
   It checks the exact file, then builds everything it needs in about
   3 minutes. This project never distributes Pioneer's firmware.
3. Try the mods in the emulator, then connect the deck over USB and click
   **Install**. Power-cycle the deck any time to go back to stock.

## For developers

```bash
python3 -m venv .venv && .venv/bin/pip install numpy pillow capstone hidapi pygame
.venv/bin/python -m emu.fwhome setup /path/to/XDJ700_v1.15.UPD --dev
```

`--dev` also places the generated files at the paths the research tools
expect (`work/`, `cache/`, `studio/assets/`, `tools/data/`, all git-ignored).
Run Mod Studio from source with `.venv/bin/python -m studio.app`.

Deck code is written in SH-4 assembly (`tools/shasm.py`) or C built with a
local `sh-elf-gcc` (`tools/build_sh_gcc.sh`). Every mod has an emulator test
that must pass before it touches hardware, for example:

```bash
.venv/bin/python tools/perform_pads.py emu
.venv/bin/python tools/hotcue8_test.py
.venv/bin/python tools/theme_switch.py check
.venv/bin/python tools/sh4test.py
```

### Layout

| Path | What |
| --- | --- |
| `emu/` | SH7724 emulator (C core + Python devices), deck UI, firmware home |
| `emu/c6x/` | TI C674x DSP emulator (work in progress) |
| `tools/` | mods, deck installers (`*_apply.py`), emulator tests, RE tools |
| `studio/` | Mod Studio (local web UI + server) |
| `mods/themes.py` | theme presets |
| `site/` | project website (GitHub Pages) |
| `docs/`, `*.md` | reverse-engineering notes (formats, protocols, addresses) |

### Safety rules (enforced by every deck tool)

- Writes only inside the deck's RAM window; the firmware-erase command never
  appears in any tool.
- Every byte is checked before it changes and read back after; anything
  unexpected stops the install.
- A read-only survey proves a mod's memory is empty on *your* deck first.
- Emulator before hardware, always.

## Firmware and copyright

No firmware, decoded firmware, emulator snapshots, screenshots of the
deck's screen or artwork lifted from it are in this repository. They are
all rebuilt on your own Mac from your own official update file
(`emu/fwhome.py`); see [SOURCES.md](SOURCES.md).

## Licence

MIT for the code in this repository (see [LICENSE](LICENSE)). It grants
nothing for Pioneer DJ / AlphaTheta firmware.
