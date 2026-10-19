# 8 hot cues at once on the PERFORM screen (design + emulator prototype)

Status 2026-10-18: **emulator prototype, RESULT: PASS (27 checks).** Never run
on the deck. Not integrated into `tools/perform_pads.py` (another session
owns that file today); integration comes after review.

Files:
- `tools/hotcue8grid/hg.c`, `hghook.S`, `build.sh` — the C module (3.6 KB,
  0 FPU instructions, built with sh-elf-gcc, so the compiler places the
  delay slots).
- `tools/hotcue8grid_test.py SNAPSHOT` — emulator test.
- `screenshots/hotcue8grid/` — `row.png` (the full screen), `held_E.png`,
  `empty_GH.png`, `overview.png`.

Snapshot used: `tools/hotcue8_emu.py` mini stick (track with hot cues A-H at
5, 13 … 61 s, the user's library left untouched) + one PERFORM tap. Every
press scenario reloads it, because a stock hot-cue press stalls the emulated
panel link (CLAUDE.md §48).

## 1. What the stock firmware does (found today, emulator + disassembly)

The A/B/C pads are touch zones 23/24/25 in the zone table 0x080D5D38
(x 116/231/345, y 232, 99×63). Their handlers do one thing: set a panel KEY
bit.

| pad | zone handler | key bit |
|---|---|---|
| A | 0x08DD0BA4 | KEY+86 (0x0B6C7A7E) bit 0x40 |
| B | 0x08DD0BB4 | KEY+86 bit 0x80 |
| C | 0x08DD0BC4 | KEY+87 (0x0B6C7A7F) bit 0x01 |
| REC | 0x08DD0BD4 | KEY+86 bit 0x20 (also re-checks the finger is still inside) |
| CALL | 0x08DD0C18 | — |

(r6 = 0x0B6C7A28 at handler entry was confirmed by trace.) The handler runs
on every panel frame while the finger is down. The snapshotter 0x8D4A7A2
copies the key bitmap into 0x0B6C40B8. The hot-cue **consumer** at
0x8D44200.. compares snapshot bytes 34/35 with the previous frame (rising
edge) and tail-jumps into the **hot-cue handler 0x8D391E6** with
`r4 = slot index 1..3`. That handler reads the slot's press state from
0x8D38F8E (tables 0x0B6C3C64 / 0x0B6C3C74 + 4·n, plus 0x0B6C42EC). Those
tables hold the hot-cue press/play state machine (0 = idle, 1/2 = active,
5/6 = transitions; written by 0x8D3858C..0x8D38680), not "is the cue set".

This is the same mechanism the beat-jump pads use: hold a panel key bit
while the finger is down. That mechanism is already proven on the real deck.

## 2. The design

**Layout.** In HOT CUE mode with the 8-pad view on, the stock row
`A B C REC CALL` (y 232..295) is replaced by eight pads A-H. Each pad is
58 px wide with a 5 px gap (63 px apart), from x 116 to x 615. They use the
stock pad colours, a green top strip when the cue is set, and white
Helvetica Neue letters (the existing `hc_glyphs.h`). Empty pads are drawn
dark with grey letters. The held pad gets an orange rim.

**Which slot is borrowed.** Pad n uses live slot `k = (n-1) % 3`.
- A, B, C use their own slots. Nothing is written; only the key bit is held.
- D-H borrow a slot: D→0, E→1, F→2, G→0, H→1. This is the same slot that
  the existing pager (`tools/hotcue8/hc.c load_page`) would use for that cue.
  When the finger goes down, the slot's 64 bytes are saved and cue n is
  written in using hc.c's field layout (state 6, number, ms, loop, 1/150 s).

**How the press reaches the stock handler.** The module chains in front of
the touch hook (pool 0x08D5278C, in front of `perform_pads`' ptouch). On a
new press (stock hit-tester idle, ZSTATE 0x1E; pads latch free) on a set
pad, it takes the press:
- It sets the slot-k key bit on every down frame.
- It never calls the stock hit-tester for that press, so no zone handler
  runs and REC can't be hit by accident.
- On release it clears the bit. Then, 8 display flushes later, it writes the
  saved slot bytes back, so slot k holds its own cue (A/B/C) again.

The stock consumer and handler 0x8D391E6 then run unchanged. A new track
(preview hash change) drops any pending restore, because the firmware has
reloaded the slots itself.

**REC / CALL / delete.**
- REC and CALL are not on the 8-pad row. They stay reachable by switching
  the 8-pad view off (stock row). So REC mode is never entered from this
  view. Pressing a set pad in REC mode would overwrite that slot, which
  would save cue D-H as A-C on the stick.
- Empty pads and the gaps between pads are swallowed. They latch until
  release, so sliding the finger onto a set pad does not fire it. An empty
  stock pad press could record a cue into the borrowed slot.
- **Delete: OPEN, must be settled before the deck.** The 700's
  MEMORY/DELETE button (right of the row) can combine with a hot-cue pad.
  If DELETE is held while D-H is pressed, the deck would delete the borrowed
  slot, which really holds A/B/C on the stick. Rule for integration: find
  the DELETE (and SHIFT, if any) key bit and swallow D-H presses while it
  is held. A-C presses are stock-correct and can pass.

**Toggling the view.** In the prototype this is just a byte (`hg_view`,
entry +8 `hg_set_view`). For integration, the proposal is that the HOT CUE
tab swipe, which pages A-C/D-F/G-H today, switches between the "8 pads"
view and the "stock row with REC/CALL" view. The 8-pad view replaces
paging, so `HCPAGE` stays 0 and the hc.c letter overlay goes away. The
module already only acts when HCPAGE == 0, PMODE == 0 (HOT CUE), MODS ON
and MODEW == 7.

## 3. What the emulator proved (tools/hotcue8grid_test.py)

- 8 pads drawn on the PERFORM screen. Gaps and empty pads are swallowed,
  and a finger sliding from an empty pad onto a set one does not fire it.
- Pad E held: slot 1 = cue E (5, 37000 ms), KEY+86/0x80 up, stock
  hit-tester untouched, slots 0/2 untouched. On release the bit drops and
  slot 1 is restored byte-exact (cue B).
- Pad A: no slot write. Pad H, sitting where stock REC was: slot 1 = H, and
  the REC zone is never latched.
- View off: the stock A zone is reached as before.
- **Handler entry (py core trace):** 0x8D391E6 is entered with r4 = 2 and the
  slot holding cue 5 (pad E), r4 = 2 / cue 8 (H), r4 = 3 / cue 6 (F),
  r4 = 1 / cue 1 (A). That is the same entry a stock B press makes
  (r4 = 2 / cue 2), and no zone handler runs for our pads.
- The delay-slot scan of the build (no branch or PC-relative load in any
  slot, §119) is clean. A literal-pool word that decodes as `bra` is
  filtered out by its out-of-module target.

**Not proven (emulator limits):** the actual jump or audio. In this lineage
the handler's state check 0x8D38F8E returns 0 for the stock press too
(tables 0x0B6C3C64/74 are all zero; stand-in DSP), so neither stock nor ours
gets past the gate. The deck is the judge.

## 4. Deck questions / risks before any install

1. **Restore timing.** On a stopped deck, holding a hot cue plays from it
   and release returns to the cue. If the engine reads the slot **at
   release** or later, the 8-flush holdoff (~130 ms) must cover it. Check:
   hold E while stopped, release, and confirm it returns to E, not B.
2. **Delete/SHIFT key bits** (see above). Settle this before the first deck
   run.
3. **Track unload / save while borrowed.** If the deck writes hot cues back
   to the stick on unload (it does for recorded cues), a borrowed slot
   could be saved. The borrow window is only the press + holdoff. The track
   hash drops a stale restore. Recommendation: also refuse a D-H press while
   the deck is in a load/eject transition (mode word check).
4. **Cave address.** HG_BASE defaults to 0x09850000 (emulator only: the old
   VF base, believed free). The deck rule applies: survey it read-only in
   idle/loaded/playing before installing (§75/§77). Better: merge into the
   hotcue8 module at 0x09790000 during integration.
5. **Pad colours.** No per-cue colours yet (rekordbox colours live in
   PCO2/PCP2; the stock 700 row doesn't show them either).

## 5. Integration plan (after review)

- Move the touch logic into the hotcue8 module (one cave, one flush hook,
  one touch hook). Retire `load_page`/letters; keep `find_cue`.
- In perform_pads, change the HOT CUE tab swipe from paging to toggling the
  view (HCSW unit → `hg_set_view`). Keep `phhc`'s REC block until paging is
  gone.
- Installer: same order as hotcue8_apply (code + zeroed bss, chain prevs,
  hooks last, with LW for both pools). The touch pool must chain in FRONT of
  perform_pads' ptouch.
- Rerun `tools/hotcue8grid_test.py` + `perform_pads.py emu` + repro_freeze
  play test.
