# XDJ-700 Test Drive — one page, that's it

**What this is:** try our XDJ-700 mods. Everything is temporary — turn the
deck off and on and it's stock again. Nothing can brick your deck. There
are deliberately NO flashing tools in here.

**Start:** double-click `XDJ-Test-Drive.command`. It sets itself up once,
then opens the Studio in your browser.

**Try these three things:**

1. **Emulator** — click Emulator → Start. A virtual XDJ-700 appears.
   Browse around. Tell us if anything looks broken.
2. **Mods on your deck** — plug the deck in by USB, play any track, quit
   music apps. The Studio's “Mods” tab installs everything (waveforms,
   PERFORM pads, themes). Or in Terminal, from this folder, run:
   `.testvenv/bin/python tools/deck_session.py --mode 3band`
   Then try the PERFORM screen — BEAT JUMP / HOT CUE / KEY SHIFT / STEMS
   tabs, waveform colours, MY SETTINGS rows. Power-cycle the deck to undo.
3. **Key shift demo (no deck needed)** — in the Studio: Tools → pick a
   track → move the Live slider. It plays shifted. Tell us how it sounds.

**Report back:** deck model + firmware (Utility screen), Mac model, what
worked, what didn't, one photo where it helps. "Nothing happened" is data.

**Rules:** if anything looks odd, stop and power-cycle the deck. Never run
anything with "flash" in the name (there isn't any). Delete this folder
when done, don't share it.
