# Deck check before 1.0 ships (round 2)

Round 1 (2026-10-25): install 19 min all PASS; key shift v3 PERFECT (no
AIFF noise); FLAC loads + plays (freeze fixed) but skipping stalled /
emergency loop; phase meter still overlapped by stock with deck 2 master;
vinyl brake length no effect; themes slow + stock stripes on VINYL BRAKE /
SYNC; Get Lucky stems no centre waveform; stems mute still slow.
Fixed offline since: FLAC seeks by binary search + no per-byte divisions
(hi-res CPU much lower), themes 11x faster switch, MY SETTINGS rows gone
(UTILITY only).

Stick OUT, deck freshly on. I install (≈13 min), then:

## 1. Readings I take (read-only, ~10 s each, you set the state)
- **Brake:** UTILITY › VINYL BRAKE LENGTH → 8X, close UTILITY → I read the
  value the brake uses (tools/brake_probe_deck.py).
- **Phase meter:** deck 1 playing, deck 2 playing FROM deck 1's stick
  (overlap visible) → I read the meter's counters (tools/pm_probe.py).
- **Themes:** pick a theme in UTILITY, light VINYL BRAKE and SYNC so the
  stripes show → I read the skip log (tools/theme_skips.py).
- **Stems:** STEMS file playing, mute VOCALS → I read the stems counters
  (tools/stems_probe.py) at once and after 10 s.

## 2. Re-tests
- FLAC: load the 96 kHz file, skip around (search buttons, hot cue, beat
  jump): keeps playing?
- Themes: how long does a switch take now?
- Get Lucky stems: centre waveform there? (was it there in 0.9.8? which
  WAVEFORM COLOR?)
