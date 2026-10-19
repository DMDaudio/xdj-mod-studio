# SOURCES — where the ignored files come from

Nothing in `.gitignore`'s "not for any public repo" section ships publicly.
Pioneer firmware, and anything copied out of it (decoded blocks, RAM
snapshots, rebuilt updates, screenshots of its screens, lifted artwork),
stays on your own Mac. The released app asks each user for their own
official firmware download and rebuilds everything below locally.

## You provide (1 file)

An official XDJ-700 firmware update (`.UPD`, e.g. v1.15 from Pioneer):
everything else derives from it. Point the tools at it:

- emulator: `emu/fw.py` (`--upd XDJ700.UPD`, or `work/MAIN.img`)
- Mod Studio / mod_builder: first positional arg / `--upd`

## Rebuilt locally (one command)

```bash
.venv/bin/python -m emu.fwhome setup /path/to/XDJ700_v1.15.UPD --dev
```

checks the file is exactly the official v1.15 update (SHA-256), then builds
in `~/Library/Application Support/XDJ Mod Studio/firmware/` (Mod Studio
does the same on first run) and, with `--dev`, copies to the repo paths:

| file(s) | what |
|---|---|
| `work/MAIN.img`, `cache/blk1.bin` | unpacked / decoded firmware image |
| `cache/emu_mainscreen.pkl.gz` | emulator snapshot (fresh boot to the main screen) |
| `studio/assets/screens.npz` | main + PERFORM screens for theme previews |
| `tools/data/hotcue_pad_edges.npy`, `stock_pad_digits.npz` | pad art cropped from the PERFORM screen at fixed positions |
| theme switch tables | built from the decoded image (`tools/theme_switch.py`) |

Other research snapshots (`cache/emu_*.pkl`) come from the tools that name
them (e.g. `tools/boot_realusb.py`). `XDJ-1000/` only if you own one.

## Never needed for a build

`screenshots/`, `backups/`, `FIRMWARE/`, `Mysettings/`,
`xdjmod/`, `ghidra/` projects, session notes (`CLAUDE.md`, `SESSION*.md`,
etc.), `*.orig`/`*.REJECTED`, `crossver/` intermediates, `sim/`.

## License notes (not legal advice)

- Our code (`.py`, `.html`, `.sh`, docs): ours to license as we please.
- `emu/assets/*.png`, `studio/assets/*`: third-party logos/artwork —
  replace with your own before publishing.
- The SH toolchain, file formats and protocols reimplemented here are
  clean-room code; the firmware they talk about is not included.
