# XDJ Mod Studio roadmap (user-agreed 2026-10-21, after v0.9.4)

Rules that hold for every version: XDJ-700 only; nothing flashed unless the
user explicitly builds a UPD; emulator before deck; never publish firmware.
Public repo = tools/make_public_repo.sh export only (agents get the PUBLIC
repo). Public builds: bake-free files swapped in, tools/scrub_app.py keeps
the builder's name out. No new Reddit post per release: edit the original
post / reply in its comments. Tester bug fixes take the next 0.9.x number
whenever they're needed.

## Shipped
- v0.9 / v0.9.1 (2026-10-19): first public release; install fix for the
  downloaded app; Updates section; centred UPD tab.
- v0.9.2 (2026-10-20): reinstall on plug-in, How it works guide, deck
  check (fw 1.15 + leftovers), sturdier USB reads (fastload flush/retry).
- v0.9.3 (2026-10-20): PERFORM pads install on Windows (font slots sized
  for Arial; PEND 0x081E03A0).
- v0.9.4 (2026-10-21): stems work when opened by double-click (Homebrew
  PATH), in-app Install stem separation, ffmpeg + ffprobe checks.
- v0.9.5 (2026-10-21): BEAT JUMP tab name on Windows (aafont squeezes the
  Arial stand-in to Neue Condensed widths, CONDENSE 0.84).
- v0.9.6 (2026-10-24): Windows parity -- the public repo/CI snapshot was
  STALE (permanent v2 + extras, phase meter module, font fix and deck
  fixes never reached the Windows zip; that one export gap explains the
  "UPD stops after 2 min", "centre colour module not built" and
  AssertionError-2802 reports). Key-shift MT freeze fix (tab inert while
  MT off, MT change resets the key), hot cue TAP pages again (swipe kept),
  phase meter reads POSL 0x0C0E6D1C (the time digits' live source; the
  old word 0x09D246E0 freezes on silicon), HLE advances POSL + adopts
  SH_POS seeks, FLAC & Opus untickable (10 of the 15-20 min), UPD
  presets (phase meter only / waveform colours only), prebuilt/deck
  fallback for source checkouts, Vinyl-brake row + linked-deck row fix.
  Gates: perform_pads emu, phasemeter_test (new, emu_track_loaded +
  synthetic grid), permanent full + pm-only boot tests, repro_freeze
  all-mods play, app smoke.

## Open tester reports -- status after 0.9.6
- Phase meter on-deck: 0.9.6 needs one hardware confirm (POSL source +
  the "flickers between ours and stock squares" report; the flush redraws
  ours every frame and the sentinel covers themed borders -- if flicker
  persists, skip the stock box painter, CLAUDE.md sec. 65 lead).
- PERFORM "window switches to the stock square meter": that is the pads
  entering KEY SHIFT/STEMS mode on the PERFORM screen (stock strip there
  is the twin painter; DCAVE2 hooks it only when centre colours are on).
  Confirm on deck with 0.9.6.
- Key shift freeze + hot-cue paging: fixes shipped in 0.9.6 (both
  emulator-gated); confirm on deck.
- Themes "don't work" (one German tester): no log yet; 0.9.6 ships the
  row reorder + [THEME] table wiring -- retest, then ask for the log
  (theme_switch_apply --survey REFUSED is the likely stop).
- "Phase meter and RGB went away after theme settings": the MY SETTINGS
  [WAVEFORM]=BLUE and [MODS]=OFF rows switch them off (app text now says
  so). Not a code bug so far.
- Mac "too new version" (one report, no exact message): the app needs
  macOS 14+; get the exact wording before acting.
- LINK: with mods on one deck, the OTHER deck couldn't load songs from
  this deck's USB over Pro DJ Link (first report). Suspect our hooks on
  the shared media fread/seek path (sm stems, vredirect, FLAC vfile layer) also
  serve link requests -> check what the link server's reads go through.
- FLAC not read on a deck: 0.9.6 install ends with the eject/reinsert
  note (types are classified at mount). Confirm it clears it.
- 3-band "doesn't work" on a user's players (probably no PWV6/PWV7 in .EXT:
  needs Track Builder > Add 3-band); RGB "occasionally bugs out with some
  tracks" (need track names).
- Emulator for an Italian user: every load -> "MEDIA or FILE COLLAPSED" +
  E-8302, colours only with "boot from reset". Needs their log/stick info.
- "Reverted when I played a song" (waiting on details).
- Requests: vinyl speed set with the jog wheel; Echo/Reverb/Phaser FX (DSP
  effect stage, same route as live key shift); waveform preview in BROWSE.

## Next 0.9.x: fixes from the user's deck session
- Install from the DOWNLOADED app on the real deck; check: phase meter,
  8 hot cues (Reddit: "didn't work"), faster stem mute, BEAT JUMP 32/64,
  vinyl brake length, FLAC/Opus, new USB reads, reinstall prompt.
- Fix what breaks; plus Windows tester reports (first full Windows install).
- Open Reddit report: "reverted when I played a song" (waiting on details).

## 0.9.6: emulator audio + OneLibrary converter
- Emulator audio fixed: real-DSP emulator runs ~8% of real time -> stutter;
  sound on Windows. Proof harness: tools/differ.py (identical state) +
  tools/dsp_audio_diag.py. Note: the other agent's fastcore hot-device
  change (2026-10-20) is behaviour-identical but showed no clear speedup.
- OneLibrary converter in the apps: tools/onelibrary_to_pdb.py (tests pass
  incl. the user's real stick; needs sqlcipher3 bundled for Mac + Windows),
  Track Builder button "Make this stick readable on the XDJ-700"; check one
  converted stick on the deck (emulator library view works since the
  usbdisk short-name fix).

## 0.9.7: permanent file v2 (beta)
- Bake centre colours, key shift (DSP image patch), stems mixer, hot cues,
  phase meter via the boot copier (built + emulator-tested, private:
  tools/permanent_build.py, tools/permanent_extras_emu.py).
- PREREQ: one real flash of a basic permanent file going fine.

## 1.0
- Everything on the list proven on a deck, installed from the downloaded app.
- Windows out of beta (after a successful Windows install by a person).
- Phase meter fixed on the deck + master deck's row from Pro DJ Link.
- Track filter (BPM range + compatible keys in BROWSE) IF a short research
  pass (emulator only) says it's doable; else 1.1. Lead: the deck already
  reads PIONEER/rekordbox/RBFLTR.DAT.
- Refreshed site + README.

## 1.1 and later (ideas)
- Track filter (if it missed 1.0).
- Waveform preview next to track names in BROWSE (Reddit request; data is
  on the stick). Audio preview in the browser: unlikely (one playback path).
- FX pads on the touch screen, Link over USB, more hot cue/loop pages,
  custom home screens.

## Not planned
- StreamingDirectPlay (partner logins/DRM). Other decks: XDJ-700 only.
