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

- v0.9.7 (2026-10-24): hotfix -- deck install crashed at job() (flac
  keyword).
- v0.9.8 (2026-10-25, salvofly's Windows list): phase meter also on the
  PERFORM screen (pm.c pm_dy -103, phasemeter_test PERFORM leg); MY
  SETTINGS hides [THEME] when theme switching isn't installed OR the popup
  is short (linked deck) so [HOT CUE] gate + [VINYL BRAKE] stay (MSKB);
  BEAT JUMP stale key bit released from rtkeep after 8 flushes with the
  finger up (the hit-tester sometimes stopped before its countdown;
  KQTAB/KSDRAW moved +0x48/+0x20); emulator audio work from 10-24.

## Open tester reports -- after 0.9.8 (salvofly, 2026-10-25)
- Key shift + MT freeze + "corrupted file": NOT reproduced (emu real DSP:
  MP3 +-2, WAV +2/-6/+6 pitch exact, no DSP error; stock DSP with a tagged
  BD8 word = harmless; no SH reader of 0x09D24DD8 besides the BD8 pusher).
  Asked: file type, USB vs LINK source, steps, exact message. Suspects:
  LINK-loaded track (same re-entrancy family as the LINK report), a format
  we haven't run (AAC/M4A), tempo far from 0 + big shift.
- 3 hot cues only: asked whether tracks have D-H + install log. The
  8-pad grid prototype (docs/HOTCUE8_GRID.md) = "display all 8" if wanted.
- RGB on one player: asked for MY SETTINGS state + same-version installs.
- "N key toggles Quantize on the BEAT JUMP tab": QUANTIZE = zone 20
  (642,232) handler KEY+91 bit 0x80; our pads end x 615 and the emu shows
  no byte-91 change on any BJ tap. Asked what "N" is.
- Suggestions: vinyl brake picker on the jog (row exists in MY SETTINGS),
  FX (Echo/Reverb/Phaser), deck-side 3-band for unanalysed tracks.

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
  this deck's USB over Pro DJ Link (first report). SOLVED TO THE
  MECHANISM (static, 2026-10-24); cause not yet proven:
  * the deck's generic Media API -- read 0x08DDFC96(buf,size,count,h,req),
    seek 0x08DE00D6, tell 0x08DE0364, eof 0x08DE0510, size 0x08DE0E7A --
    dispatches BY DEVICE TYPE; USB = type 3, implemented by the six slots
    the FLAC layer replaces (close 0x08DDFD28, fread_direct 0x08DDFFD4,
    seek 0x08DE0298, tell 0x08DE04FC, eof 0x08DE0768, size 0x08DE0F48)
    plus the media read server's fread pool 0x08D5FFA8 (its only user --
    what versions/stems hook). The server also dispatches type 4 ->
    0x08C95760 (a cached-descriptor reader: lock helper 0x8C65860, cache
    descriptor @handle; IDENTIFY -- plausibly the link/stream reader or
    preview) and type 6 -> 0x08E1D2E0.
  * 24 call sites of the generic read spread over browse/db/waveform/
    loader modules, and the link stack is on the deck by name
    (RemoteDBServer / LocalDBServer / CONTENTLINK tasks; "dbserver" is
    Pioneer's link protocol) -> the link server almost certainly rides
    the same USB slots. Our FLAC vfile sits under EVERY USB file
    consumer incl. link serving; stems under the direct slot; versions
    under the pool.
  * foreign reads are handle-checked and mostly pass through BY DESIGN,
    so the ranked suspects: (1) NO re-entrancy serialisation in vfile/sm
    (one global decode ARENA; the link task entering while the player
    task is inside = state corruption -- built and tested single-
    consumer only), (2) a link-path access pattern one of the six
    wrappers mishandles (5-arg generic read vs 4-arg slots; seek/tell/
    size combos), (3) vredirect's new-handle trailer probe (3 extra
    ops) tripping a dbserver response timeout, (4) innocent: link reads
    take the type-4 reader and bypass all our hooks.
  * NEXT (deck, one session, instrumented -- NO deck-code changes
    before the evidence): rebuild vfile + sm with counters (entries per
    slot fn, foreign-handle rate, passthrough vs virtualised, arena
    re-entry hits), link two decks, peer loads 3 tracks, read the
    counters over USB. Then serialise (busy flag -> straight to the
    chained stock fn, no state touched) or gate by handle owner
    per what fired. Native reentrancy test can extend
    tools/xflac_vfile_test.py (blocking mock + second call).
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
- Emulator audio (2026-10-24 session, CLAUDE.md 126): real time is out of
  reach for the cosim -- the SH interpreter sustains ~77 Mcyc/s vs the 500
  needed (JIT-scale project), and the SH is the PCM pump so the DSP can't
  be decoupled. What shipped instead: honest core speedups (+15% cosim,
  all differ-A/B-proven: CS6 word batching, C-side HPIA/HPID reads,
  sync throttling, interpreter commit rework), BURST playback in DspSound
  (sub-real-time hosts get clean quarter-second passages at exact pitch
  instead of the machine gun; automatic smooth mode near 100%), and sound
  on Windows via pygame.mixer (macaudio.make_output; mock-proven, needs
  one audible check on a real Windows wheel). Harnesses: tools/
  dsp_rate_bench.py, dsp_burst_test.py, pygame_output_test.py.
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
