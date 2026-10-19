# DSP execution proof — design package (offline, no deck, no writes)

Goal: prove third-party bytes execute on the C674x by writing a magic
word from DSP code and reading it back from the host. No audio touched
if placed right. Nothing here runs without a deck session.

## Verified facts (objdump, tools/dspdis.py, this repo)

- File offset == DSP address in the load image (0x1E654 site confirmed).
- MT-branch render setup at 0x1E644-0x1E654:
  `intspu b4,b4 ; mpysp b5,b4,b6 ; stw b6,*+b14(320)` (DP[320] store).
- The instruction AFTER the store, 0x1E658 `mvk .L2 0,b25`, is the hook
  site: branch there to a trampoline that (1) executes the displaced
  `mvk 0,b25`, (2) writes magic to a spare word, (3) branches back to
  0x1E65C. All original semantics preserved + one store.
- Free-space candidates (file zero runs, need VMA + deck confirmation):
  0x88B0 (120 B), 0x9CCE (72 B). NOT 0x134/0x420 (likely headers).
  Trampoline needs ~10 words + 1 data word, 32 B aligned.
- Spare word: put it INSIDE the trampoline allocation (data word after
  code). Host reads it via the generic HPI read accessor (any DSP addr).
  Zero side effects by construction.
- Delivery: HPI runtime writes to DSP program RAM (assumption: L2 writable
  via HPI — verify by read-after-write BEFORE arming the branch).
  Undo = rewrite original words. Sequencing: write trampoline -> read back
  verify -> arm branch -> poll magic (~seconds, needs playback + MT on,
  since 0x1E654 is the MT-branch path) -> disarm -> verify restore.

## Still open (needs the deck + a toolchain decision)

- Trampoline byte encoding. Hand-encoding stalled on field-fitting
  (dst=[27:23] verified on 3 samples; cst16 position unsolved; one sample
  may be a misaligned decode). Two routes: (a) programmatic field fit
  over many objdump lines then round-trip verify, or (b) fetch TI CGT
  assembler (free download, version pinning needed).
- Confirm free-space VMAs are really unused (deck HPI readback).
- Confirm HPI writes stick in program RAM (read-after-write test).
- Spare-word HPI readability from SH RAM (pick accessor + BR address).

## Cheaper control proof (no DSP code at all)

Writing word0 ratio / MT bit through the EXISTING host path and hearing
pitch follow proves the documented chain live. Read-only first
(mt_capture.py states), writes only with explicit go. Do this BEFORE
any trampoline work — if the chain doesn't respond, the trampoline is
moot anyway.
