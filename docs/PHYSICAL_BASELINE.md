# PHYSICAL-DECK BASELINE (confirmed on hardware, 2026-09-27 -> 2026-10-03)

(Restored 2026-10-04 from session transcript after work/ was wiped. The
raw captures it referenced -- work/deck-captures/ (popup RAM diffs
playing+paused, vinyl-hold keylog, session logs, beaj_apply.log) -- are
LOST; the conclusions below are what survives.)

Reboot wipes everything below; deck serial <deck serial>, fw 1.15,
VID 0x2B73 PID 0x0008.

## Proven on the deck
- **Beat-jump 12B:** OUT=Fwd4 IN=Rev4 fire on a playing track
  (tools/beatjump_apply.py, fail-stop per byte, `--revert` restores).
- **Touch injection:** Vinyl Break flashes + audibly engages from shell
  BW writes alone (300+ volley runs). Hot-cue A coords silent (Y=0x0001
  fails the deck's own >=32 gate).
- **Touch decode (live BR):** Y=PAY+22/23, P=PAY+24/25 (BE u16),
  PAY+21=touch-down. PAY+4/5 = TEMPO FADER (not X). Real X unfound.
- **Audio path:** DMA0 all-ch+DMAOR+DMARS=0 AND DMA1 ch0-5+DMAOR=0 with
  audible playback. DSP shadow 0x0B6C515C+: play flag +0x28 bit4
  (1=playing/looping, 0=paused/gap; 4 states proven) + track words.
  (2026-10-04 emu: MAIN<->DSP is the C6x HPI at CS6 0x18000000; see
  CLAUDE.md §34.)
- **Jog = key events** (JogMov + JogRoundFwd/Rev + DirectionRev in live
  KL). Rotary moves the on-deck highlight via a counter (2026-10-04:
  KY 0x42/0x43 bump u16 @0x0B6C7B48).
- **USB shell rules:** single combined opens only; ~10 rapid opens wedge
  enumeration (minutes to recover); quit music/MIDI apps first; replug
  grants ~1 session.
- **Popup memory:** deck never shows the popup (emu cause found
  2026-10-04: GPIO overcurrent pin, CLAUDE.md §32).

## Never attempted
Zero cave executions. Zero flash writes. Zero bricks.

## Ready for next deck session
1. PERFORM BEAT JUMP tab 16 (2-byte size table, emu PASS; the old
   deck apply tool is gone -- sizes install with the pads now).
2. tools/rgb_overview_apply.py [rgb|3band|blue] (first cave run).

## 2026-10-04 (deck <deck serial>)
- **Beat jump 16 (size-table edit) CONFIRMED on hardware** by the user
  (PERFORM BEAT JUMP tab, 16).
- **RGB overview v2 + RGB centre CONFIRMED on hardware** (user photo +
  video, 2026-10-04 evening); browse-colour bug fixed offline after.

## Ready for next deck session (2026-10-05)
ONE command, stick OUT of the deck, after a fresh power-on:
`.venv/bin/python tools/deck_session.py --size 16 --mode rgb`
(surveys -> beat jump 16 -> RELOOP re-point -> overview+snapshot ->
version/menu area survey -> [eject stick] -> centre (1000 MK2 maths) ->
version switching -> MOD MENU). All emu PASS. NEW on hardware: centre
1000-maths, snapshot fix, version switching, mod menu.
