# Real-time emulator audio — the rebuild plan (2026-10-26)

## Where we are (measured, M-series Mac, real-DSP snapshot playing)

`tools/dsp_rate_bench.py`: **14.5 % of real time** (SH 72.7 Mcyc/s of
500 MHz, DSP 43.6 Mcyc/s of 300 MHz, audio 12.8k of 44.1k words/s).
Audio plays in clean bursts with gaps (burst mode in `emu/dspsound.py`).

Wall-time split while playing (`sample` + `tools/io_profile.py`):

| part | share | notes |
|---|---|---|
| C6x DSP core (`sp_cycle`, `c6x_run`, `mread`/`mwrite`) | ~45 % | software-pipelined loops dominate |
| SH-4 interpreter (`step`, `core_run`, memory access) | ~40 % | after today's fetch shortcut |
| Python device crossings (2DG, DMAC, HPI, timers) | ~14 % | was ~35 % before the 10-24 work |

Compiler flags (`-O3`, `-mcpu=native`) gain nothing: the cost is in the
structure (decode every instruction every time, per-cycle event loops).

Real time needs **both** chips about **7x** faster.

## Phases

1. **SH-4: decode once, run blocks** (est. 2-3x on the SH side).
   A per-4 KB-page cache of pre-decoded instructions (handler index +
   operands), executed as straight-line blocks up to the next branch;
   pages are invalidated when RAM inside them is written (bitmap check in
   the store path; our mods write code at runtime). Same `step()`
   semantics, behind `XDJ_SH_BLOCKS=0/1`; gate = `tools/differ.py` A/B
   MATCH + `sh4test` + `core_lockstep`.
2. **C6x: cheaper cycle loop** (est. 1.5-2x on the DSP side). The core
   already runs pre-decoded `Ins` records; the cost is the per-cycle
   pipeline/event bookkeeping and the SPLOOP buffer stepping. Batch idle
   cycles, skip empty event slots, specialise the hot SPLOOP kernels. Gate
   = `tools/c6x_test.py` + the deterministic DSP-alone hash
   (`scratchpad/dspdet.py` pattern) + replay oracle.
3. **Devices in C** (est. +10-15 %): 2DG register file + DMAC status +
   INTC/timer reads served in C (the remaining Python crossings).
4. **JIT** (the step to real time): translate hot SH-4 blocks and hot C6x
   loops to native code (arm64 first, x86-64 for Windows). Weeks of work;
   phases 1-3 lay the ground (block boundaries, invalidation, exact
   accounting) that a JIT needs anyway.

Expected after 1-3: roughly 30-40 % of real time (smoother bursts, much
shorter gaps). Real time itself: phase 4.

## Ground rules (from the fast-path work)

- Every speed-up must be **exact**: same registers, RAM, device state and
  the same step/cycle **COMMIT** points (the DSP clock reads committed
  cycles). `XDJ_FASTPATH=0` turns the SH shortcuts off for A/B.
- `d_cyc` (DSP cycle counter) differs between two identical `--live`
  runs already: ignore it in A/B, compare everything else.
- Pool-hooked firmware routines (lt_rgb, stems, …) must never be bypassed
  by a native fast path: hook targets can change at runtime.

## Done 2026-10-26: 2DG display lists in C

`core_tdg_run` (emu/fastcore.c) runs every 2DG list op (fill, copy,
textured quad incl. scaling, polygon, line, WPR, LCOFS, NOP/INT, SYNC,
TRAP) as an exact port of `emu/tdg.py`; Python passes the register file
in and applies the results. It stops BEFORE any op it can't do (unknown
op, non-RAM memory, WPR >= 0x100) and Python continues the same list.
Off with `XDJ_TDG_C=0`, and automatically while `draw_log`/`fb_hook` are
in use. Gates: differ A/B MATCH (track_loaded, mainscreen, wavplay
--live), sh4test 44/44, light_emu_test PASS, 0 fallbacks seen.
Speed while playing: 13.9 % -> 15.4 % of real time.
(The old C register mirror `tdg_r8/tdg_wr8` is dead code: never called.)

## Done 2026-10-26: SH core phase 1 (part 1)

- **Data-page cache** (`dtlb_get` in emu/fastcore.c): loads/stores hit a
  256-entry cache of host page pointers keyed on (virtual page, SR.MD,
  low-flash map), generation-stamped per `core_run`; only pages `loc()`
  maps wholly into one buffer. `XDJ_DTLB=0` off.
- **Decode-once table** (`g_cls[65536]` + `fast_step`): each opcode is
  classified once; 64 hot forms (~90 % of executed instructions on the
  playing snapshot) run directly, the rest go through `step()`.
  `XDJ_DTAB=0` off. Proof: `sh tools/dtab/run_selftest.sh` runs every
  classed opcode through both paths from random states over fake memory
  and logging fake devices (2.28M cases, 0 mismatches; a planted bug is
  caught), plus differ A/B MATCH x3, sh4test 44/44, core_lockstep 150k.
- Speed: SH-only snapshot ~95 -> ~130 M steps/s; playing with the real
  DSP 15.4 % -> 17.6 % of real time (the SH is now well under half of the
  wall time there; the C6x core and per-slice glue are the next targets).

## Done 2026-10-26: audio chip (C6x) delay-loop fast-forward

- Profile (playing, real DSP): the DSP is idle ~60 % of the time (already
  skipped); of its working cycles ~30 % are ONE busy-wait routine at
  0xC004BEE0, `for (volatile int i = 0; i < n; i++);` (SPLOOPW, 14 cycles
  per count; every call is delay(1000) = 14,044 cycles, called from an
  interrupt handler with interrupts off).
- c6xcore.c dff_*: the first call with a given n is LEARNED (run
  normally, accepted only if the code bytes match, no interrupt/Python
  op, only the known registers + stack word changed, to the closed-form
  values); later calls are replayed: whole call at once when it fits the
  run slice, else (interrupts off = nothing outside can change it) the
  call is FROZEN: c6x_run counts its cycles down and applies the recorded
  end state at the exact cycle. `cpu.settle()` (called by every status
  getter and dspdev.state) re-runs a frozen call from its entry state to
  the current cycle, so any observation equals plain execution. Reset /
  set_pc / set_cycle / code reload cancel it. XDJ_DSP_DFF=0 = off.
- Proof: tools/dsp_ab.py hashes all DSP RAM, registers, control regs,
  cycle, the DAC stream and the SH state at N checkpoints (each one a
  mid-run settle): identical with/without on wavplay (8 + 5 checkpoints)
  and playing7 (8); c6x_test 13/13; dsp_audio_diag balanced; a snapshot
  saved mid-delay reloads and plays.
- Speed while playing: ~17 % -> ~19 % of real time.

## 2026-10-26 (later): SH-4 block translator (native arm64), exact

Built in emu/fastcore.c (block engine) + emu/shjit.c (codegen) +
emu/a64enc.h (encoder):
- blocks = runs of decode-table instructions, optionally ending in a
  delayed branch + slot (core_run's K_DBR semantics); 64K-slot cache.
- native code per block: inline page-cache memory, reference handler for
  misses/rare ops, own-page store exit, exact it/steps/cycles per exit.
- chaining: blocks jump straight to the next compiled block (pc/key/
  epoch/idle checked; special pcs excluded); every block checks its own
  opcodes against memory on entry (code changes from any writer).
- switches: XDJ_BLOCKS=0, XDJ_JIT=0. Windows/x86: interpreter only.
Proof: tools/jit/run_selftest.sh (99 encodings vs the assembler; random
blocks incl. delayed branches, faults, device io, both memory paths, 0
mismatches; 3000/3000 code-change refusals), differ MATCH (track_loaded,
mainscreen), dsp_ab IDENTICAL (wavplay), sh4test 44/44.

Speed: SH-only snapshot ~101 -> ~120 M steps/s; playing case unchanged
(~15-18 %), because the wall time is now spread evenly (profile, playing):
Python 28 %, SH C side 23 %, native code 23.5 %, C6x DSP 22.6 %.

Next steps toward 90 % (each needed, none enough alone):
1. Codegen quality: keep SH registers in host registers inside a block
   (write back before helpers/exits), direct-patched chains, fewer
   per-exit stores -> native code 2-3x.
2. Block cache churn: ~150k recompiles per 100M steps; hot-block
   retention (2-way sets / no flush on collision).
3. Python: the slice loop + device ticks + device callbacks into C.
4. C6x: a block translator like this one for the DSP (SPLOOP kernels).

## 2026-10-26 (night): play mode -- the two chips on two threads

The two real chips run in PARALLEL; the emulator ran them in lock-step.
Play mode (dspdev.py) puts the C6x on its own worker thread once the
DSP is booted (BOOT_SETTLE = 200M DSP cycles of lock-step first, and
after every snapshot restore): the SH side only queues the DSP time it
owes (Condition variable, 60k-cycle chunks); back-pressure beyond
XDJ_DSP_MAXLAG (default 3M cycles) makes the SH wait, so the DSP can
never trail by more than ~10 ms emulated. irq_lock guards the flags
both threads touch (HPIC, INTC raises; the C core's IFR updates are
C11 atomics -- c6x_ifr_or/and), run_lock keeps code rewrite / reset /
snapshot off a running core, and state() drains what is owed before
anyone looks. `drain()`/`stop_thread()` quiesce on demand.
NOT cycle-exact (interleavings differ run to run) -- exact mode stays
the default and every test/validation tool runs exact. XDJ_PLAY=1
enables play mode (own DSP thread + 131072-step device slices via
fastcpu._slice); the app sets it; XDJ_DSP_THREAD=1 threads the DSP
without the bigger slices. Playback + burst audio work (diag: gaps 2,
max lag 32 words); snapshot save/reload works mid-play; light_emu_test
FULLSET (boot, real UTILITY presses, theme to LIGHT and back) PASSES
in play mode. chunk 60k vs 250k/1M: no difference (Python/GIL
crossings dominate, not chunk size).
Exactness proof for the exact-mode default: c6x_test 13/13, sh4test
44/44, core_lockstep 200k, differ A/B MATCH x2 (track_loaded,
mainscreen), dsp_ab stable, jit/dtab selftests PASS.
Speed (wavplay, M-series): exact 24 % of real time; play mode 32.4 %
(SH 162 Mcyc/s, DSP 97 Mcyc/s). Note for A/B across checkouts: run
both sides in the SAME tree -- a git worktree run of the same commit
produces different dsp_ab hashes (missing untracked FIRMWARE/ or cache
state; harmless, dac counts identical).

## 2026-10-26 night: fewer Python crossings, wider fast table (play 32 -> ~35 %)

- BUG FIXED (since 10-24): fastcpu's CS6 crossings set `d.hpia` on the
  SH7724 device instead of `d.dspemu.hpia`, so the audio-chip model never
  adopted HPIA values the C mirror had taken (byte-path HPIA stores).
  Latent until more HPIA traffic stayed in C; now adopted correctly.
- HPIA word stores (aligned 16/32-bit to 0x18040000) stay in the C mirror
  (the byte path already did): ~39k Python crossings per 0.4 emulated s
  gone. dspdev's HPIA write has no side effects, so this is exact.
- 2DG register file 0x00-0xFF owned by C while running (tdg_on, the old
  unused tdg_pad slot): word and byte reads/writes served from the
  mirror; only SCLR (word 0, starts a list) crosses. fastcpu flushes C ->
  Python before every Python 2DG touch and adopts Python -> C after;
  TDG.state() pulls first, TDG.restore() bumps `gen` so the core
  re-adopts. XDJ_TDG_MIRROR=0 = old behaviour. 2DG crossings 56k -> 7k
  per 30M steps.
- Block table: BLK_BITS 18, slot = (pc>>1 ^ pc>>19) & mask (C + emitted
  chain code). Plain indexing made hot code 256 KB apart collide: ~470k
  rebuilds/s, 96k recompiles per 3 s -> 36k. (Wall effect small.)
- 12 more decode-once classes (fast_step + native emitters): mov.w
  @(R0,Rm); mov.b/w @(d,Rm),R0 and R0,@(d,Rm); mov.b/w Rm,@-Rn; sts/lds
  FPSCR; sts.l MACL,@-Rn / lds.l @Rm+,MACL; dmulu.l (3 cycles, cls_cyc()).
  step() traffic while playing 15.6M -> 5.0M per 300M steps; blocks run
  longer before falling back. dtab selftest 76 classes 0 mismatches, JIT
  selftest 20000 blocks 0 mismatches.
- Gates: differ A/B vs the previous commit's library MATCH (track_loaded,
  mainscreen, 60M steps), dsp_ab IDENTICAL to the previous commit at all
  6 checkpoints, sh4test 44/44, c6x 13/13, dsp_audio_diag balanced,
  light_emu_test PASS.
- Speed, play mode, alternating old/new runs on a busy Mac: 26.7-32.0 %
  -> 33.3-38.3 %.
- Where the time goes now (SH thread, DSP held): ~41 % of real time on
  one core; ~1/3 Python (run loop: ~33k passes per emulated second, ends
  forced by 2DG list-end IRQs, TMU, DMA, idle skips; each pass ~15-30 us),
  ~1/4 native code, the rest interpreter/C. Next levers: device ticks +
  next-event calc in C (lazy TMU counters), native handling of the COMMIT
  boundary (~8 % of instructions still interpreted there), ldc/stc SR in
  the fast table.
- KNOWN, NOT FROM THIS CHANGE: tools/app_usb_probe.py never sees a load
  ("mode=0", red=560) on the user's library -- same on the previous build.
