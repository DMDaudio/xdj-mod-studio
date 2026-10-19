#!/usr/bin/env python3
"""pv_phase.py -- phase-vocoder core in SH-4 (shasm).

Fixed-point conventions (match pv.py semantics):
  mags:    int16 per bin (scaled analysis magnitudes)
  phases:  Q16 TURNS (1.0 turn = 2pi = 65536; numpy angle a (rad) =
           int(a/2pi*65536) & 0xFFFF). Wrapping is free (u16 overflow!).
  omega:   Q16 turns expected advance per bin (precomputed table).

Kernels (each bit-checked vs a numpy model):
  PEAKS: local maxima over mags (exact, incl. edge rules:
         pk[0] = a0>=a1; pk[-1] = aN>aN-1; mid strict-greater left,
         greater-or-equal right).
  NEAREST: nearest peak index per bin, ties -> left (exact).
  PROPAGATE: new = prev + omega + wrap(dphi - omega), with peak-lock
         (new[b] = new[nearest[b]] + (ph0[b]-ph0[nearest[b]])) and
         transient blend skipped here (float-side concern; the integer
         path -- indices, peaks, maps, wrapped deltas -- is exact).
  HANN: in-place Q15 window apply, round-half-up (exact).
  SINS/COSS: exp(j*phase) from one shared 257-entry Q15 sin table
         (idx = phase>>8, frac lerp >>8 floor; cos via (idx+64)&255;
         table[256] = table[0] guard). Exact vs integer model.

  in (PEAKS):  r4=MAG r5=NBINS r6=PK(out u8)
  in (NEAREST): r4=PK r5=NBINS r6=NEAR(out u16)
  in (PROPAGATE): r4=PREV r5=PH0 r6=PH1 r7=OMEGA r8=NEAR r9=OUT r10=NBINS
       (all u16 arrays; OUT = new phases Q16)
  in (HANN): r4=X(in/out s16) r5=W(Q15) r6=N
  in (SINS): r4=PHASE(u16 turns) r5=TAB(257 s16) r6=N r7=OUTSIN(s16)
  in (COSS): r4=PHASE(u16 turns) r5=TAB(257 s16) r6=N r8=OUTCOS(s16)
  in (ATAN2): r4=RE(s16) r5=IM(s16) r6=OUT(u16 Q16 turns) r7=N
       r8=ATANTAB(16 u16 Q16); 16-iter CORDIC vectoring, small-vector
       <<10 prescale, (0,0)->0; uses r0-r3,r9-r12
  in (MIX): r4=PS(s16) r5=PC(s16) r6=AS(s16) r7=AC(s16) r8=TRW(Q15
       scalar) r9=ORT(s16) r10=OIT(s16) r11=N; uses r0-r3,r12,r14
  in (BITREV): r4=RE r5=IM r6=N; in-place bit-reversal.
  in (FFTSTAGES): r4=RE r5=IM r6=TWB(twiddle pairs) r7=N r8=STAGES;
       DIT stages, >>1 per stage (1/N total), tw idx = k*tstep;
       tstep spilled on stack (peek via R0-only mov.l @(8,r15),r0).
  in (CONJ): r4=IM r5=N; negate imag in place (IFFT trick).
  All end rts+nop.

  EMU GOTCHA (2026-10-03): py-core AND Rm,Rn does NOT set T (real SH-4
  sets T iff result is zero). Use TST for zero-tests (BITREV does).
  R0-only @(disp,Rm) loads are fine (tstep peek uses one).

Run: .venv/bin/python tools/pv_phase.py (self-tests vs numpy).
"""
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from shasm import assemble
from sh4box import run_code
import numpy as np

PEAKS = """
peaks:
  tst r5, r5
  bt pdone
  cmp/eq #1, r5
  bt pone
  mov.w @r4, r0
  mov r4, r1
  add #2, r1
  mov.w @r1, r1
  cmp/ge r1, r0
  movt r2
  mov.b r2, @r6
  add #2, r4
  add #1, r6
  add #-2, r5
  tst r5, r5
  bt plast
ploop:
  add #2, r4
  mov.w @r4, r2
  cmp/gt r0, r1
  bf pno
  cmp/ge r2, r1
  bf pno
  mov #1, r3
  bra pok
  nop
pno:
  mov #0, r3
pok:
  mov.b r3, @r6
  mov r1, r0
  mov r2, r1
  add #1, r6
  dt r5
  bf ploop
plast:
  cmp/gt r0, r1
  movt r1
  mov.b r1, @r6
  bra pdone
  nop
pone:
  mov #1, r0
  mov.b r0, @r6
pdone:
  rts
  nop
"""

OUTP, MAGB, PKB, NEARB = 0x0B200000, 0x0B210000, 0x0B220000, 0x0B230000
PREVB, PH0B, PH1B, OMEAB, OMSB, OUTB = (0x0B240000, 0x0B250000, 0x0B260000,
                                        0x0B270000, 0x0B280000, 0x0B290000)
OUTB2 = 0x0B290000
PREO_B = 0x0B480000
MAGB = 0x0B490000
WINB, WINW = 0x0B2A0000, 0x0B2B0000
SINB, COSB = 0x0B2C0000, 0x0B2D0000
SNOB, CSOB = 0x0B2E0000, 0x0B2F0000
REB, IMB, ANGB, ATAB = 0x0B300000, 0x0B310000, 0x0B320000, 0x0B330000
PSB, PCB, ASB, ACB = 0x0B340000, 0x0B350000, 0x0B360000, 0x0B370000
ORTB, OITB = 0x0B380000, 0x0B390000
FFTR, FFTI, FFTW = 0x0B3A0000, 0x0B3C0000, 0x0B3E0000
MAGR, MAGI, MAGO = 0x0B400000, 0x0B410000, 0x0B420000
MAGLA, MAGLB, MAGLO = 0x0B440000, 0x0B450000, 0x0B460000

# Integer sqrt of re^2+im^2, digit by digit (2 bits of the 32-bit sum per
# iteration, taken from the TOP via SHLD with negative counts - r9 = -30..0).
# Restoring recurrence, with root the value BEFORE this digit's doubling:
#   rem  = (rem << 2) | d ; trial = 4*root + 1 ;
#   root = 2*root + (rem >= trial) ; if so rem -= trial
# (trial = 4*root+1 is the digit test for b=1 in v' = 4v+d, root' = 2root+b.
# NOTE: the branch must IMMEDIATELY follow the compare -- shll/shlr/shar/rotl
# write T with the bit shifted out on real SH-4 (Ghidra SLEIGH: "clear or set
# T"), so a shift between cmp and bf silently branches on the wrong T. That
# was the one real bug in this kernel; tools/sh_taudit.py now guards it.
#   in: r4=RE(s16) r5=IM(s16) r6=OUT(s16) r7=N ; clobbers r0-r3, r8-r11
#   out: floor(sqrt(v)), saturated to 32767 (a caller that keeps the spectrum
#        s16 never hits the clamp: |v| <= 2*32767^2 -> root <= 46340 only
#        when a component is -32768, which the chain never produces).
# NOTE shld-with-negative-count: the manual ORs the low bits of (v << (32-k))
# into the result; the emulator's plain logical shift right differs there, but
# both agree on bits 0..1 for k >= 2, which is all this kernel reads.
MAG = """
mag:
  tst r7, r7
  bt mgdone
  mov #64, r11
  shll8 r11
  shll r11
  add #-1, r11
mgloop:
  mov.w @r4, r0
  muls.w r0, r0
  sts macl, r1
  mov.w @r5, r0
  muls.w r0, r0
  sts macl, r0
  add r1, r0
  mov r0, r1
  mov #0, r2
  mov #0, r8
  mov #-30, r9
  mov #16, r10
mgbit:
  mov r1, r0
  shld r9, r0
  shll2 r2
  tst #1, r0
  bt mgno0
  add #1, r2
mgno0:
  shlr r0
  tst #1, r0
  bt mgno1
  add #2, r2
mgno1:
  mov r8, r3
  shll2 r3
  add #1, r3
  cmp/hs r3, r2
  bf mgnoh
  sub r3, r2
  shll r8
  add #1, r8
  bra mgd2
  nop
mgnoh:
  shll r8
mgd2:
  add #2, r9
  dt r10
  bf mgbit
  cmp/hs r11, r8
  bf mgok
  mov r11, r8
mgok:
  mov.w r8, @r6
  add #2, r4
  add #2, r5
  add #2, r6
  dt r7
  bf mgloop
mgdone:
  rts
  nop
"""

BITREV = """
bitrev:
  tst r6, r6
  bt brdone
  mov #1, r0
  mov #0, r1
brloop:
  cmp/hi r0, r6
  bf brdone
  mov r6, r2
  shlr r2
brwhile:
  tst r2, r1
  bt brwend
  xor r2, r1
  shlr r2
  bra brwhile
  nop
brwend:
  xor r2, r1
  cmp/gt r0, r1
  bf brnext
  mov.l r0, @-r15
  mov r0, r3
  shll r3
  add r4, r3
  mov r1, r2
  shll r2
  add r4, r2
  mov.w @r3, r0
  mov.w @r2, r2
  mov.w r2, @r3
  mov r1, r2
  shll r2
  add r4, r2
  mov.w r0, @r2
  mov.l @r15, r0
  mov r0, r3
  shll r3
  add r5, r3
  mov r1, r2
  shll r2
  add r5, r2
  mov.w @r3, r0
  mov.w @r2, r2
  mov.w r2, @r3
  mov r1, r2
  shll r2
  add r5, r2
  mov.w r0, @r2
  mov.l @r15+, r0
brnext:
  add #1, r0
  bra brloop
  nop
brdone:
  rts
  nop
"""

FFTSTAGES = """
fftstages:
  tst r8, r8
  bt fdone
  mov #2, r9
  mov #-15, r14
  mov r7, r0
  shlr r0
  mov.l r0, @-r15
fstage:
  mov #0, r12
  mov r9, r10
  shlr r10
fgloop:
  mov r9, r10
  shlr r10
  mov r12, r11
  add r10, r11
  mov #0, r13
fkloop:
  mov r9, r10
  shlr r10
  cmp/ge r10, r13
  bt fkdone
  mov r12, r0
  add r13, r0
  shll r0
  mov r4, r1
  add r0, r1
  mov.w @r1, r2
  mov r5, r1
  add r0, r1
  mov.w @r1, r3
  mov.l r2, @-r15
  mov.l r3, @-r15
  mov.l @(8,r15), r0
  mov r13, r1
  muls.w r1, r0
  sts macl, r1
  shll2 r1
  mov r6, r0
  add r1, r0
  mov.w @r0, r10
  add #2, r0
  mov.w @r0, r3
  mov r11, r0
  add r13, r0
  shll r0
  mov r4, r1
  add r0, r1
  mov.w @r1, r2
  mov r5, r1
  add r0, r1
  mov.w @r1, r0
  muls.w r2, r10
  sts macl, r1
  muls.w r0, r3
  sts macl, r11
  sub r11, r1
  shad r14, r1
  muls.w r2, r3
  sts macl, r11
  muls.w r0, r10
  sts macl, r0
  add r0, r11
  shad r14, r11
  mov.l @r15+, r3
  mov.l @r15+, r2
  mov r2, r0
  add r1, r2
  sub r1, r0
  shar r2
  shar r0
  mov.l r2, @-r15
  mov.l r0, @-r15
  mov r3, r0
  add r11, r3
  sub r11, r0
  shar r3
  shar r0
  mov.l r3, @-r15
  mov.l r0, @-r15
  mov r9, r10
  shlr r10
  mov r12, r11
  add r10, r11
  mov r11, r0
  add r13, r0
  shll r0
  mov r5, r1
  add r0, r1
  mov.l @r15+, r0
  mov.w r0, @r1
  mov r12, r0
  add r13, r0
  shll r0
  mov r5, r1
  add r0, r1
  mov.l @r15+, r0
  mov.w r0, @r1
  mov r11, r0
  add r13, r0
  shll r0
  mov r4, r1
  add r0, r1
  mov.l @r15+, r0
  mov.w r0, @r1
  mov r12, r0
  add r13, r0
  shll r0
  mov r4, r1
  add r0, r1
  mov.l @r15+, r0
  mov.w r0, @r1
  add #1, r13
  bra fkloop
  nop
fkdone:
  add r9, r12
  cmp/hi r12, r7
  bt fgloop
  shll r9
  mov.l @r15+, r0
  shlr r0
  mov.l r0, @-r15
  dt r8
  bf fstage
  add #4, r15
fdone:
  rts
  nop
"""

CONJ = """
conj:
  tst r5, r5
  bt cjdone
cjloop:
  mov.w @r4, r0
  neg r0, r0
  mov.w r0, @r4
  add #2, r4
  dt r5
  bf cjloop
cjdone:
  rts
  nop
"""

MIX = """
mix:
  tst r11, r11
  bt mxdone
  mov #64, r12
  shll8 r12
  shll r12
  add #-1, r12
  sub r8, r12
  mov #-15, r14
mxloop:
  mov.w @r4, r0
  mov.w @r5, r1
  mov.w @r6, r2
  mov.w @r7, r3
  muls.w r1, r12
  sts macl, r1
  muls.w r3, r8
  sts macl, r3
  add r3, r1
  mov #64, r3
  shll8 r3
  add r3, r1
  shad r14, r1
  mov.w r1, @r9
  muls.w r0, r12
  sts macl, r0
  muls.w r2, r8
  sts macl, r2
  add r2, r0
  mov #64, r2
  shll8 r2
  add r2, r0
  shad r14, r0
  mov.w r0, @r10
  add #2, r4
  add #2, r5
  add #2, r6
  add #2, r7
  add #2, r9
  add #2, r10
  dt r11
  bf mxloop
mxdone:
  rts
  nop
"""

ATAN2 = """
atan2:
  tst r7, r7
  bt atdone
  mov #0, r12
atloop:
  mov.w @r4, r0
  exts.w r0, r0
  mov.w @r5, r1
  exts.w r1, r1
  mov #0, r2
  mov r0, r3
  or r1, r3
  tst r3, r3
  bt atzero
  mov r0, r3
  cmp/ge r12, r3
  bt atxabs
  neg r3, r3
atxabs:
  mov r1, r11
  cmp/ge r12, r11
  bt atyabs
  neg r11, r11
atyabs:
  or r11, r3
  mov #2, r11
  shll8 r11
  cmp/ge r3, r11
  bf atnobst
  shll2 r0
  shll2 r0
  shll2 r0
  shll2 r0
  shll2 r0
  shll2 r1
  shll2 r1
  shll2 r1
  shll2 r1
  shll2 r1
atnobst:
  cmp/ge r12, r0
  bt atxpos
  neg r0, r0
  neg r1, r1
  mov #64, r2
  shll8 r2
  shll r2
atxpos:
  mov #16, r10
  mov #0, r9
atiter:
  mov r0, r3
  shad r9, r3
  mov r1, r11
  shad r9, r11
  cmp/ge r12, r1
  bf atyneg
  add r11, r0
  sub r3, r1
  mov.w @r8, r3
  add r3, r2
  bra atnext
  nop
atyneg:
  sub r11, r0
  add r3, r1
  mov.w @r8, r3
  sub r3, r2
atnext:
  add #2, r8
  add #-1, r9
  dt r10
  bf atiter
  add #-32, r8
atzero:
  mov.w r2, @r6
  add #2, r4
  add #2, r5
  add #2, r6
  dt r7
  bf atloop
atdone:
  rts
  nop
"""

SINS = """
sins:
  tst r6, r6
  bt sndone
sinloop:
  mov.w @r4, r0
  extu.w r0, r0
  mov r0, r1
  shlr8 r1
  extu.b r1, r1
  mov r0, r2
  extu.b r2, r2
  mov r5, r3
  add r1, r3
  add r1, r3
  mov.w @r3, r3
  mov r5, r0
  add r1, r0
  add r1, r0
  add #2, r0
  mov.w @r0, r0
  sub r3, r0
  muls.w r0, r2
  sts macl, r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  add r3, r0
  mov.w r0, @r7
  add #2, r4
  add #2, r7
  dt r6
  bf sinloop
sndone:
  rts
  nop
"""

COSS = """
coss:
  tst r6, r6
  bt csdone
cosloop:
  mov.w @r4, r0
  extu.w r0, r0
  mov r0, r1
  shlr8 r1
  extu.b r1, r1
  add #64, r1
  extu.b r1, r1
  mov r0, r2
  extu.b r2, r2
  mov r5, r3
  add r1, r3
  add r1, r3
  mov.w @r3, r3
  mov r5, r0
  add r1, r0
  add r1, r0
  add #2, r0
  mov.w @r0, r0
  sub r3, r0
  muls.w r0, r2
  sts macl, r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  add r3, r0
  mov.w r0, @r8
  add #2, r4
  add #2, r8
  dt r6
  bf cosloop
csdone:
  rts
  nop
"""

HANN = """
hann:
  tst r6, r6
  bt hdone
hloop:
  mov.w @r5, r0
  mov.w @r4, r1
  muls.w r0, r1
  sts macl, r0
  mov #64, r1
  shll8 r1
  add r1, r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  shar r0
  mov.w r0, @r4
  add #2, r4
  add #2, r5
  dt r6
  bf hloop
hdone:
  rts
  nop
"""

# 2026-10-03 FIX: this kernel took ONE omega table and added then subtracted
# it, which cancels -- it propagated the raw analysis delta (new = prev+dphi)
# and could not time-stretch for any ratio != 1. Now the two tables the
# algorithm actually needs: OMEGA_A (analysis-hop advance b*Ha/N turns,
# subtracted inside the wrap) and OMEGA_S (synthesis-hop advance b*Hs/N, added
# outside):
#   out[b] = prev[b] + om_s[b] + wrap(ph1[b] - ph0[b] - om_a[b])
#   in (PROP): r4=PREV r5=PH0 r6=PH1 r7=OMEGA_A r8=OMEGA_S r9=OUT r10=NBINS
PROP = """
prop1:
  tst r10, r10
  bt pdone1
p1:
  mov.w @r4, r0
  mov.w @r5, r1
  mov.w @r6, r2
  mov.w @r7, r3
  extu.w r0, r0
  extu.w r1, r1
  extu.w r2, r2
  extu.w r3, r3
  sub r1, r2
  extu.w r2, r2
  sub r3, r2
  extu.w r2, r2
  add #2, r7
  mov.w @r8, r3
  extu.w r3, r3
  add r3, r2
  extu.w r2, r2
  add r0, r2
  extu.w r2, r2
  mov.w r2, @r9
  add #2, r4
  add #2, r5
  add #2, r6
  add #2, r8
  add #2, r9
  dt r10
  bf p1
pdone1:
  rts
  nop
"""

# Magnitude interpolation between two consecutive analysis frames:
#   out[b] = a + (((b - a) * frac + 32768) >> 16), frac Q16 (same 64-bit
#   product + round-half-up shape as tools/resample.py, proven bit-exact).
#   in (MAGL): r4=MAG_A r5=MAG_B r6=OUT r7=N r8=FRAC(Q16)
MAGL = """
magl:
  tst r7, r7
  bt mldone
  mov #64, r9
  shll r9
  shll8 r9
ml_loop:
  mov.w @r4, r0
  mov.w @r5, r1
  sub r0, r1
  dmuls.l r1, r8
  sts macl, r1
  sts mach, r2
  add r9, r1
  cmp/hi r1, r9
  movt r3
  add r3, r2
  shlr16 r1
  shll16 r2
  or r2, r1
  add r0, r1
  mov.w r1, @r6
  add #2, r4
  add #2, r5
  add #2, r6
  dt r7
  bf ml_loop
mldone:
  rts
  nop
"""

# polar -> complex resynthesis: re = (mag*cos + 16384) >> 15,
# im = (mag*sin + 16384) >> 15, round-half-up on the Q15 sin/cos.
# No clamp needed: |mag| <= 32767 and |sin| <= 32768 keeps the product under
# 2^30-16384, so the result cannot exceed s16.
#   in (RECT): r4=MAG r5=SIN r6=COS r7=OUTRE r8=OUTIM r9=N
RECT = """
rect:
  tst r9, r9
  bt rcdone
  mov #-15, r10
  mov #64, r11
  shll8 r11
rc_loop:
  mov.w @r4, r0
  mov.w @r6, r1
  muls.w r0, r1
  sts macl, r1
  add r11, r1
  shad r10, r1
  mov.w @r5, r3
  muls.w r0, r3
  sts macl, r3
  add r11, r3
  shad r10, r3
  mov.w r1, @r7
  mov.w r3, @r8
  add #2, r4
  add #2, r5
  add #2, r6
  add #2, r7
  add #2, r8
  dt r9
  bf rc_loop
rcdone:
  rts
  nop
"""

PROP2 = """
prop2:
  tst r10, r10
  bt pdone2
  mov r4, r7
  mov r5, r11
p2:
  mov.w @r6, r0
  mov r7, r1
  add r0, r1
  add r0, r1
  mov.w @r1, r1
  mov.w @r5, r2
  mov r11, r3
  add r0, r3
  add r0, r3
  mov.w @r3, r3
  sub r3, r2
  extu.w r2, r2
  add r1, r2
  extu.w r2, r2
  mov.w r2, @r9
  add #2, r5
  add #2, r6
  add #2, r9
  dt r10
  bf p2
pdone2:
  rts
  nop
"""

# Bounded phase locking (the PROP2 shape plus two gates).  PROP2 locks EVERY
# bin to its nearest peak, which re-phases the whole noise floor: measured on
# a broadband mix it costs ~9 dB of reconstruction SNR and on a
# sweep+noise+low-tone signal it destroys the low partial entirely (0.9 dB).
# A bin is only part of a partial if it is (a) within W bins of the peak --
# a Hann main lobe is +-2 bins at N=1024 -- and (b) within 3/8 of the peak's
# magnitude, so noise-floor bins keep their own propagated phase.
#
# in: r4=NEW r5=PH0 r6=NEAR r7=MAG r8=WIDTH r9=OUT r10=NB
PROP3 = """
prop3:
  tst r10, r10
  bt pdone3
  mov r5, r11
  mov #0, r13
p3:
  mov.w @r6, r0
  mov r0, r12
  sub r13, r12
  cmp/gt r8, r12
  bt p3n
  neg r12, r12
  cmp/gt r8, r12
  bt p3n
  mov r7, r1
  add r13, r1
  add r13, r1
  mov.w @r1, r1
  shll2 r1
  shll r1
  mov r7, r2
  add r0, r2
  add r0, r2
  mov.w @r2, r2
  cmp/gt r2, r1
  bf p3n
  mov r4, r3
  add r0, r3
  add r0, r3
  mov.w @r3, r3
  mov.w @r5, r2
  mov r11, r1
  add r0, r1
  add r0, r1
  mov.w @r1, r1
  sub r1, r2
  extu.w r2, r2
  add r3, r2
  extu.w r2, r2
  mov r2, r0
  bra p3st
p3n:
  mov r4, r3
  add r13, r3
  add r13, r3
  mov.w @r3, r0
p3st:
  mov.w r0, @r9
  add #2, r5
  add #2, r6
  add #2, r9
  add #1, r13
  dt r10
  bf p3
pdone3:
  rts
  nop
"""

# Region-of-influence phase locking with a spectral-contrast gate (PROP4).
# Two measurements forced this shape (tools/pv_lab.py `lock`):
#   * unbounded locking (PROP2, every bin -> its nearest peak) costs ~8 dB on
#     broadband material and ~9 dB on a sweep+noise mix, because it re-phases
#     the whole noise floor;
#   * bounding the lock to the peak's main lobe (PROP3) does not recover it
#     (28.0 vs 27.7 dB) -- in noise almost every bin IS within a bin of a
#     peak, so the window is not the discriminator.
# The discriminator is contrast: a bin is only part of a *tonal* partial if
# its peak dominates the spectrum 8 bins to either side.  Measured on one
# frame, the tone keeps 87% of its energy locked at every threshold while the
# noise bins drop out, so broadband material is left alone.
#
#   lock iff  |near - b| <= WIDTH
#        and  mag[near] >= CONTRAST * (mag[near-8] + mag[near+8])
#
# r11 = CONTRAST as a shift count (0, 1 or 2 -> x1, x2, x4).  The probes read
# MAGPAD, which is padded with 8 zero bins at each end, so bin edges need no
# clamping and a zero floor (the DC/Nyquist end) simply passes the gate.
# in: r4=NEW r5=PH0 r6=NEAR r7=MAGPAD r8=WIDTH r9=OUT r10=NB r11=CONTRAST
PROP4 = """
prop4:
  tst r10, r10
  bt pdone4
  mov r5, r12
  mov #0, r13
p4:
  mov.w @r6, r0
  mov r0, r1
  sub r13, r1
  cmp/gt r8, r1
  bt p4n
  neg r1, r1
  cmp/gt r8, r1
  bt p4n
  mov r7, r1
  add r0, r1
  add r0, r1
  add #16, r1
  mov.w @r1, r1
  mov r7, r2
  add r0, r2
  add r0, r2
  mov.w @r2, r2
  mov r7, r3
  add r0, r3
  add r0, r3
  add #32, r3
  mov.w @r3, r3
  add r3, r2
  mov r11, r3
p4sh:
  tst r3, r3
  bt p4c
  shll r2
  add #-1, r3
  bra p4sh
p4c:
  cmp/gt r1, r2
  bt p4n
  mov r4, r3
  add r0, r3
  add r0, r3
  mov.w @r3, r3
  mov.w @r5, r2
  mov r12, r1
  add r0, r1
  add r0, r1
  mov.w @r1, r1
  sub r1, r2
  extu.w r2, r2
  add r3, r2
  extu.w r2, r2
  mov r2, r0
  bra p4st
p4n:
  mov r4, r3
  add r13, r3
  add r13, r3
  mov.w @r3, r0
p4st:
  mov.w r0, @r9
  add #2, r5
  add #2, r6
  add #2, r9
  add #1, r13
  dt r10
  bf p4
pdone4:
  rts
  nop
"""

# Region-of-influence locking with a STABLE offset (PROP5) -- the shipping
# rule.  PROP2/3/4 took the bin-to-peak phase offset from the CURRENT frame's
# analysis phases (phi0[b] - phi0[near]), which equals the textbook anchor
# offset only while the partial stands still.  Measured at R=1 that costs
# nothing on a stationary tone (-0.16 dB) but destroys every moving partial:
# -23 dB on a 0.15-bins-per-frame glissando, -40 dB at 0.30, because when the
# peak migrates across a bin the recomputed offset jumps by a whole lobe step.
#
# Reading the offset off the accumulated phases instead (prev[b] - prev[near],
# with prev = a snapshot of last frame's propagated phases taken BEFORE this
# frame's lock overwrites them) makes the offset the same quantity for b and
# for its peak, so it can only drift smoothly and never jump.  The contrast
# gate from PROP4 is kept, since broadband material still needs it.
#
#   lock iff  |near - b| <= WIDTH
#        and  mag[near] >= CONTRAST * (mag[near-8] + mag[near+8])
#   value =  new[near] + (prev[b] - prev[near])
#
# in: r4=NEW r5=PREV_OLD r6=NEAR r7=MAGPAD r8=WIDTH r9=OUT r10=NB r11=CONTRAST
PROP5 = """
prop5:
  tst r10, r10
  bt pdone5
  mov r5, r12
  mov #0, r13
p5:
  mov.w @r6, r0
  mov r0, r1
  sub r13, r1
  cmp/gt r8, r1
  bt p5n
  neg r1, r1
  cmp/gt r8, r1
  bt p5n
  mov r7, r1
  add r0, r1
  add r0, r1
  add #16, r1
  mov.w @r1, r1
  mov r7, r2
  add r0, r2
  add r0, r2
  mov.w @r2, r2
  mov r7, r3
  add r0, r3
  add r0, r3
  add #32, r3
  mov.w @r3, r3
  add r3, r2
  mov r11, r3
p5sh:
  tst r3, r3
  bt p5c
  shll r2
  add #-1, r3
  bra p5sh
p5c:
  cmp/gt r1, r2
  bt p5n
  mov r4, r3
  add r0, r3
  add r0, r3
  mov.w @r3, r3
  mov.w @r5, r2
  mov r12, r1
  add r0, r1
  add r0, r1
  mov.w @r1, r1
  sub r1, r2
  extu.w r2, r2
  add r3, r2
  extu.w r2, r2
  mov r2, r0
  bra p5st
p5n:
  mov r4, r3
  add r13, r3
  add r13, r3
  mov.w @r3, r0
p5st:
  mov.w r0, @r9
  add #2, r5
  add #2, r6
  add #2, r9
  add #1, r13
  dt r10
  bf p5
pdone5:
  rts
  nop
"""

NEAREST = """
nearest:
  tst r5, r5
  bt ndone
  mov r5, r7
  mov #0, r0
  mov #0, r1
nfwd:
  mov r4, r2
  add r0, r2
  mov.b @r2, r3
  tst r3, r3
  bt nfwd0
  mov r0, r1
nfwd0:
  mov r6, r2
  add r0, r2
  add r0, r2
  mov.w r1, @r2
  add #1, r0
  dt r5
  bf nfwd
  mov r7, r5
  add #-1, r5
  mov r5, r1
nbwd:
  mov r4, r2
  add r5, r2
  mov.b @r2, r3
  tst r3, r3
  bt nbwd0
  mov r5, r1
nbwd0:
  mov r6, r2
  add r5, r2
  add r5, r2
  mov.w @r2, r3
  mov r5, r0
  sub r1, r0
  mov r5, r2
  sub r3, r2
  muls.w r0, r0
  sts macl, r0
  muls.w r2, r2
  sts macl, r2
  cmp/gt r0, r2
  bf nbwd1
  mov r6, r0
  add r5, r0
  add r5, r0
  mov.w r1, @r0
nbwd1:
  add #-1, r5
  dt r7
  bf nbwd
ndone:
  rts
  nop
"""


def run_hann(x, w):
    import struct
    code = assemble(HANN)
    c, mem, res = run_code(code, regs={4: WINB, 5: WINW, 6: len(x)},
                           data={WINB: struct.pack("<%dh" % len(x), *x),
                                 WINW: struct.pack("<%dh" % len(x), *w)})
    raw = bytes(mem.r8(WINB + i) for i in range(2 * len(x)))
    return list(struct.unpack("<%dh" % len(x), raw)), res


def model_hann(x, w):
    return [(a * b + 16384) // 32768 for a, b in zip(x, w)]


def build_sintab():
    import math
    tab = [int(round(32767 * math.sin(2 * math.pi * i / 256)))
           for i in range(256)]
    tab = [max(-32768, min(32767, v)) for v in tab]
    tab.append(tab[0])
    return tab


def run_sincos(ph, tab):
    import struct
    tblob = struct.pack("<%dh" % len(tab), *tab)
    phblob = struct.pack("<%dH" % len(ph), *[p % 65536 for p in ph])
    n = len(ph)
    code = assemble(SINS)
    c, mem, res = run_code(code, regs={4: COSB, 5: SINB, 6: n, 7: SNOB},
                           data={SINB: tblob, COSB: phblob})
    raw = bytes(mem.r8(SNOB + i) for i in range(2 * n))
    got_s = list(struct.unpack("<%dh" % n, raw)) if n else []
    code = assemble(COSS)
    c, mem, res = run_code(code, regs={4: COSB, 5: SINB, 6: n, 8: CSOB},
                           data={SINB: tblob, COSB: phblob})
    raw = bytes(mem.r8(CSOB + i) for i in range(2 * n))
    got_c = list(struct.unpack("<%dh" % n, raw)) if n else []
    return got_s, got_c, res


def model_sincos(ph, tab):
    gs, gc = [], []
    for p in ph:
        p &= 0xFFFF
        idx = (p >> 8) & 0xFF
        f = p & 0xFF
        d = tab[idx + 1] - tab[idx]
        gs.append(tab[idx] + (d * f) // 256)
        ci = (idx + 64) & 0xFF
        d = tab[ci + 1] - tab[ci]
        gc.append(tab[ci] + (d * f) // 256)
    return gs, gc


def build_atantab():
    import math
    return [int(round(65536 * math.atan(2.0 ** -i) / (2 * math.pi)))
            for i in range(16)]


def run_atan2(re, im, atab):
    import struct
    n = len(re)
    data = {REB: struct.pack("<%dh" % n, *re),
            IMB: struct.pack("<%dh" % n, *im),
            ATAB: struct.pack("<16H", *atab)}
    code = assemble(ATAN2)
    c, mem, res = run_code(code, regs={4: REB, 5: IMB, 6: ANGB, 7: n,
                                       8: ATAB}, data=data)
    raw = bytes(mem.r8(ANGB + i) for i in range(2 * n))
    return list(struct.unpack("<%dH" % n, raw)), res


def model_atan2(re, im, atab):
    out = []
    for x0, y0 in zip(re, im):
        x, y, a = int(x0), int(y0), 0
        if x == 0 and y == 0:
            out.append(0)
            continue
        if abs(x) < 512 and abs(y) < 512:
            x <<= 10
            y <<= 10
        if x < 0:
            x, y, a = -x, -y, 32768
        sh = 0
        for i in range(16):
            tx, ty = x >> sh, y >> sh
            if y >= 0:
                x, y, a = x + ty, y - tx, a + atab[i]
            else:
                x, y, a = x - ty, y + tx, a - atab[i]
            sh += 1
        out.append(a & 0xFFFF)
    return out


def run_mix(ps, pc, as_, ac, trw):
    import struct
    n = len(ps)
    pk = lambda xs: struct.pack("<%dh" % n, *xs)
    data = {PSB: pk(ps), PCB: pk(pc), ASB: pk(as_), ACB: pk(ac)}
    code = assemble(MIX)
    c, mem, res = run_code(code, regs={4: PSB, 5: PCB, 6: ASB, 7: ACB,
                                       8: trw, 9: ORTB, 10: OITB,
                                       11: n}, data=data)
    gr = bytes(mem.r8(ORTB + i) for i in range(2 * n))
    gi = bytes(mem.r8(OITB + i) for i in range(2 * n))
    return (list(struct.unpack("<%dh" % n, gr)),
            list(struct.unpack("<%dh" % n, gi)), res)


def model_mix(ps, pc, as_, ac, trw):
    w1 = 32767 - trw
    orr = [((w1 * c + trw * a + 16384) // 32768)
           for c, a in zip(pc, ac)]
    oii = [((w1 * s + trw * a + 16384) // 32768)
           for s, a in zip(ps, as_)]
    return orr, oii


def run_blend(prop, ana, trw, tab, atab):
    pss, pcs, _ = run_sincos(prop, tab)
    ass, acs, _ = run_sincos(ana, tab)
    orr, oii, _ = run_mix(pss, pcs, ass, acs, trw)
    return run_atan2(orr, oii, atab)


def run_conj(x):
    import struct
    n = len(x)
    code = assemble(CONJ)
    c, mem, res = run_code(code, regs={4: IMB, 5: n},
                           data={IMB: struct.pack("<%dh" % n, *x)})
    raw = bytes(mem.r8(IMB + i) for i in range(2 * n))
    return list(struct.unpack("<%dh" % n, raw)), res


def run_fft(xr, xi, tw, limit=2000000):
    import struct
    n = len(xr)
    assert n == len(xi)
    data = {FFTR: struct.pack("<%dh" % n, *xr),
            FFTI: struct.pack("<%dh" % n, *xi),
            FFTW: struct.pack("<%dh" % (2 * len(tw)),
                              *[v for p in tw for v in p])}
    code = assemble(BITREV)
    c, mem, res = run_code(code, regs={4: FFTR, 5: FFTI, 6: n}, data=data,
                           limit=limit)
    code = assemble(FFTSTAGES)
    data2 = {FFTR: bytes(mem.r8(FFTR + i) for i in range(2 * n)),
             FFTI: bytes(mem.r8(FFTI + i) for i in range(2 * n)),
             FFTW: data[FFTW]}
    stages = n.bit_length() - 1
    c, mem, res = run_code(code, regs={4: FFTR, 5: FFTI, 6: FFTW, 7: n,
                                       8: stages}, data=data2, limit=limit)
    gr = bytes(mem.r8(FFTR + i) for i in range(2 * n))
    gi = bytes(mem.r8(FFTI + i) for i in range(2 * n))
    return (list(struct.unpack("<%dh" % n, gr)),
            list(struct.unpack("<%dh" % n, gi)), res)


def run_peak(mags):
    import struct
    code = assemble(PEAKS)
    c, mem, res = run_code(code, regs={4: MAGB, 5: len(mags), 6: PKB},
                           data={MAGB: struct.pack("<%dh" % len(mags),
                                                   *mags)})
    return [mem.r8(PKB + i) for i in range(len(mags))], res


def run_nearest(pk):
    code = assemble(NEAREST)
    c, mem, res = run_code(code, regs={4: PKB, 5: len(pk), 6: NEARB},
                           data={PKB: bytes(pk)})
    import struct
    raw = bytes(mem.r8(NEARB + i) for i in range(2 * len(pk)))
    return list(struct.unpack("<%dH" % len(pk), raw)), res


def model_nearest(pk):
    n = len(pk)
    if n == 0:
        return []
    near = [0] * n
    last = 0
    for b in range(n):
        if pk[b]:
            last = b
        near[b] = last
    nxt = n - 1
    for b in range(n - 1, -1, -1):
        if pk[b]:
            nxt = b
        if abs(b - nxt) < abs(b - near[b]):
            near[b] = nxt
    return near


M64 = 65536


def model_prop(prev, ph0, ph1, om_a, om_s, near, lock=True):
    n = len(prev)
    plain = [0] * n
    for b in range(n):
        d = (ph1[b] - ph0[b] - om_a[b]) % M64
        plain[b] = (prev[b] + om_s[b] + d) % M64
    if not lock:
        return plain
    out = [0] * n
    for b in range(n):
        nb = near[b]
        out[b] = (plain[nb] + (ph0[b] - ph0[nb])) % M64
    return out


def run_prop(prev, ph0, ph1, om_a, om_s, near, lock=True):
    import struct
    code = assemble(PROP)
    n = len(prev)
    pack = lambda xs: struct.pack("<%dH" % len(xs), *[x % M64 for x in xs])
    data = {PREVB: pack(prev), PH0B: pack(ph0), PH1B: pack(ph1),
            OMEAB: pack(om_a), OMSB: pack(om_s), OUTB: bytes(2 * n)}
    c, mem, res = run_code(code, regs={4: PREVB, 5: PH0B, 6: PH1B, 7: OMEAB,
                                       8: OMSB, 9: OUTB, 10: n}, data=data)
    raw = bytes(mem.r8(OUTB + i) for i in range(2 * n))
    gotp1 = list(struct.unpack("<%dH" % n, raw))
    if not lock:
        return gotp1, res
    code = assemble(PROP2)
    c, mem, res = run_code(code, regs={4: OUTB, 5: PH0B, 6: NEARB, 9: OUTB2,
                                       10: n}, data={PH0B: pack(ph0),
                                                      NEARB: pack(near),
                                                      OUTB: struct.pack(
                                                          "<%dH" % n, *gotp1)})
    raw = bytes(mem.r8(OUTB2 + i) for i in range(2 * n))
    return list(struct.unpack("<%dH" % n, raw)), res


def model_prop3(plain, ph0, near, mag, w):
    """Bounded + magnitude-gated identity phase locking."""
    n = len(plain)
    out = [0] * n
    for b in range(n):
        p = near[b]
        if abs(p - b) <= w and mag[b] * 8 >= mag[p]:
            out[b] = (plain[p] + (ph0[b] - ph0[p])) % M64
        else:
            out[b] = plain[b]
    return out


def run_prop3(plain, ph0, near, mag, w):
    import struct
    code = assemble(PROP3)
    n = len(plain)
    pk = lambda xs: struct.pack("<%dH" % len(xs), *[x % M64 for x in xs])
    data = {PREVB: pk(plain), PH0B: pk(ph0), NEARB: pk(near),
            MAGB: struct.pack("<%dh" % n, *mag), OUTB2: bytes(2 * n)}
    c, mem, res = run_code(code, regs={4: PREVB, 5: PH0B, 6: NEARB,
                                       7: MAGB, 8: w, 9: OUTB2, 10: n},
                           data=data)
    raw = bytes(mem.r8(OUTB2 + i) for i in range(2 * n))
    return list(struct.unpack("<%dH" % n, raw)), res


def model_prop4(plain, ph0, near, mag, w, contrast):
    """Region-of-influence locking gated on spectral contrast.  mag is read
    with 8 zero bins of padding at each end (the SH MAGPAD buffer)."""
    n = len(plain)
    mp = [0] * 8 + list(mag) + [0] * 8
    out = [0] * n
    k = 1 << contrast
    for b in range(n):
        p = near[b]
        if abs(p - b) <= w and mag[p] >= k * (mp[p] + mp[p + 16]):
            out[b] = (plain[p] + (ph0[b] - ph0[p])) % M64
        else:
            out[b] = plain[b]
    return out


def run_prop4(plain, ph0, near, mag, w, contrast):
    import struct
    code = assemble(PROP4)
    n = len(plain)
    pk = lambda xs: struct.pack("<%dH" % len(xs), *[x % M64 for x in xs])
    pad = [0] * 8 + list(mag) + [0] * 8
    data = {PREVB: pk(plain), PH0B: pk(ph0), NEARB: pk(near),
            MAGB: struct.pack("<%dh" % len(pad), *pad), OUTB2: bytes(2 * n)}
    c, mem, res = run_code(code, regs={4: PREVB, 5: PH0B, 6: NEARB, 7: MAGB,
                                       8: w, 9: OUTB2, 10: n, 11: contrast},
                           data=data)
    raw = bytes(mem.r8(OUTB2 + i) for i in range(2 * n))
    return list(struct.unpack("<%dH" % n, raw)), res


def model_prop5(new, prev, near, mag, w, contrast):
    """PROP5: region-of-influence locking with a snapshot-stable offset."""
    n = len(new)
    mp = [0] * 8 + list(mag) + [0] * 8
    out = [0] * n
    k = 1 << contrast
    for b in range(n):
        p = near[b]
        if abs(p - b) <= w and mag[p] >= k * (mp[p] + mp[p + 16]):
            out[b] = (new[p] + (prev[b] - prev[p])) % M64
        else:
            out[b] = new[b]
    return out


def run_prop5(new, prev, near, mag, w, contrast):
    import struct
    code = assemble(PROP5)
    n = len(new)
    pk = lambda xs: struct.pack("<%dH" % len(xs), *[x % M64 for x in xs])
    pad = [0] * 8 + list(mag) + [0] * 8
    data = {PREVB: pk(new), PREO_B: pk(prev), NEARB: pk(near),
            MAGB: struct.pack("<%dh" % len(pad), *pad), OUTB2: bytes(2 * n)}
    c, mem, res = run_code(code, regs={4: PREVB, 5: PREO_B, 6: NEARB, 7: MAGB,
                                       8: w, 9: OUTB2, 10: n, 11: contrast},
                           data=data)
    raw = bytes(mem.r8(OUTB2 + i) for i in range(2 * n))
    return list(struct.unpack("<%dH" % n, raw)), res


def run_mag(re, im):
    """mags[b] = min(floor(sqrt(re[b]^2+im[b]^2)), 32767) -- the SH kernel.
    Limit is generous: MAG is ~600 instructions per bin (16 digits)."""
    import struct
    n = len(re)
    code = assemble(MAG)
    c, mem, res = run_code(code, regs={4: MAGR, 5: MAGI, 6: MAGO, 7: n},
                           data={MAGR: struct.pack("<%dh" % n, *re),
                                 MAGI: struct.pack("<%dh" % n, *im)},
                           limit=2000 * n + 1000)
    raw = bytes(mem.r8(MAGO + i) for i in range(2 * n))
    return list(struct.unpack("<%dh" % n, raw)), res


def model_mag(re, im):
    import math
    return [min(32767, math.isqrt(a * a + b * b)) for a, b in zip(re, im)]


def run_mag_lerp(ma, mb, frac):
    import struct
    n = len(ma)
    code = assemble(MAGL)
    data = {MAGLA: struct.pack("<%dh" % n, *ma),
            MAGLB: struct.pack("<%dh" % n, *mb)}
    c, mem, res = run_code(code, regs={4: MAGLA, 5: MAGLB, 6: MAGLO, 7: n,
                                       8: frac}, data=data, limit=200 * n)
    raw = bytes(mem.r8(MAGLO + i) for i in range(2 * n))
    return list(struct.unpack("<%dh" % n, raw)), res


def model_mag_lerp(ma, mb, frac):
    return [a + (((b - a) * frac + 32768) >> 16) for a, b in zip(ma, mb)]


def run_rect(mag, sinv, cosv):
    import struct
    n = len(mag)
    code = assemble(RECT)
    pk = lambda xs: struct.pack("<%dh" % n, *xs)
    data = {MAGO: pk(mag), SNOB: pk(sinv), CSOB: pk(cosv)}
    c, mem, res = run_code(code, regs={4: MAGO, 5: SNOB, 6: CSOB, 7: ORTB,
                                       8: OITB, 9: n}, data=data,
                           limit=200 * n)
    gr = bytes(mem.r8(ORTB + i) for i in range(2 * n))
    gi = bytes(mem.r8(OITB + i) for i in range(2 * n))
    return (list(struct.unpack("<%dh" % n, gr)),
            list(struct.unpack("<%dh" % n, gi)), res)


def model_rect(mag, sinv, cosv):
    re = [((m * c + 16384) >> 15) for m, c in zip(mag, cosv)]
    im = [((m * s + 16384) >> 15) for m, s in zip(mag, sinv)]
    return re, im


def model_peak(a0):
    a0 = np.array(a0)
    n = len(a0)
    pk = np.zeros(n, bool)
    if n == 1:
        return [True]
    pk[1:-1] = (a0[1:-1] > a0[:-2]) & (a0[1:-1] >= a0[2:])
    pk[0] = a0[0] >= a0[1]
    pk[-1] = a0[-1] > a0[-2]
    return [bool(x) for x in pk]


def main():
    rng = np.random.default_rng(7)
    bad = 0
    for t in range(60):
        n = int(rng.integers(2, 300))
        a = [int(x) for x in rng.integers(-30000, 30000, n)]
        got, res = run_peak(a)
        want = model_peak(a)
        if [bool(x) for x in got] != want:
            bad += 1
            if bad < 3:
                print("MISMATCH", a, got, want)
    assert bad == 0, bad
    print(f"PEAKS: 60/60 exact {res}")
    bad = 0
    for t in range(60):
        n = int(rng.integers(1, 200))
        pk = [int(x) for x in rng.integers(0, 2, n)]
        if not any(pk):
            pk[int(rng.integers(0, n))] = 1
        got, _ = run_nearest(pk)
        want = model_nearest(pk)
        if got != want:
            bad += 1
            if bad < 3:
                print("NEAREST MISMATCH", pk, got, want)
    assert bad == 0, bad
    print("NEAREST: 60/60 exact")
    bad = 0
    for t in range(40):
        n = int(rng.integers(1, 150))
        prev = [int(x) for x in rng.integers(0, 65536, n)]
        ph0 = [int(x) for x in rng.integers(0, 65536, n)]
        ph1 = [int(x) for x in rng.integers(0, 65536, n)]
        oma = [int(x) for x in rng.integers(0, 65536, n)]
        oms = [int(x) for x in rng.integers(0, 65536, n)]
        pk = [int(x) for x in rng.integers(0, 2, n)]
        if not any(pk):
            pk[0] = 1
        near = model_nearest(pk)
        got, _ = run_prop(prev, ph0, ph1, oma, oms, near)
        want = model_prop(prev, ph0, ph1, oma, oms, near)
        if got != want:
            bad += 1
            if bad < 3:
                print("PROP MISMATCH", n, got[:8], want[:8])
    assert bad == 0, bad
    print("PROPAGATE: 40/40 exact (two omega tables)")
    bad = 0
    for t in range(40):
        n = int(rng.integers(1, 150))
        prev = [int(x) for x in rng.integers(0, 65536, n)]
        ph0 = [int(x) for x in rng.integers(0, 65536, n)]
        ph1 = [int(x) for x in rng.integers(0, 65536, n)]
        oma = [int(x) for x in rng.integers(0, 65536, n)]
        oms = [int(x) for x in rng.integers(0, 65536, n)]
        near = list(range(n))
        got, _ = run_prop(prev, ph0, ph1, oma, oms, near, lock=False)
        want = model_prop(prev, ph0, ph1, oma, oms, near, lock=False)
        if got != want:
            bad += 1
    assert bad == 0, bad
    print("PROPAGATE (unlocked): 40/40 exact")
    # semantic check, independent of the model: feed a KNOWN instantaneous
    # frequency (analysis delta = om_a + delta) and require the propagated
    # advance to be om_s + delta -- i.e. ratio 1 keeps timing, ratio != 1
    # really does stretch. The old single-table kernel could not pass this.
    bad = 0
    for (ha, hs) in ((512, 512), (512, 256), (512, 1024), (256, 700)):
        n = 257
        nb = 129
        oma = [int(round(65536 * b * ha / 2048)) % 65536 for b in range(nb)]
        oms = [int(round(65536 * b * hs / 2048)) % 65536 for b in range(nb)]
        delta = 65536 * 0.017                     # +0.017 turn detune
        ph0 = [int(x) for x in rng.integers(0, 65536, nb)]
        ph1 = [(p + a + int(delta)) % 65536 for p, a in zip(ph0, oma)]
        prev = [int(x) for x in rng.integers(0, 65536, nb)]
        got, _ = run_prop(prev, ph0, ph1, oma, oms, list(range(nb)), lock=False)
        for b in range(nb):
            adv = (got[b] - prev[b]) % 65536
            want = (oms[b] + int(delta)) % 65536
            if adv != want:
                bad += 1
                if bad < 4:
                    print("PROP-SEM MISMATCH", ha, hs, b, adv, want)
    assert bad == 0, bad
    print("PROPAGATE: instantaneous frequency carried exactly at 4 hop ratios")
    bad = 0
    rp3 = np.random.default_rng(0xC0FFEE)
    for t in range(40):
        n = int(rp3.integers(1, 150))
        plain = [int(x) for x in rp3.integers(0, 65536, n)]
        ph0 = [int(x) for x in rp3.integers(0, 65536, n)]
        near = [int(min(n - 1, max(0, b + int(rp3.integers(-4, 5)))))
                for b in range(n)]
        mag = [int(rp3.integers(0, 32768)) for _ in range(n)]
        w = int(rp3.integers(0, 6))
        if t < 4:
            w = [0, 1, 2, 3][t]
            mag[:min(8, n)] = [0, 32767, 100, 4000, 1, 32767, 30000, 5][:n]
        got, _ = run_prop3(plain, ph0, near, mag, w)
        want = model_prop3(plain, ph0, near, mag, w)
        if got != want:
            bad += 1
            if bad < 3:
                i = next(j for j in range(n) if got[j] != want[j])
                print("PROP3 MISMATCH", n, "w", w, i, got[i], want[i])
    assert bad == 0, bad
    print("PROP3: 40/40 exact (bounded + magnitude-gated phase locking)")
    bad = 0
    rp4 = np.random.default_rng(0xBEEF)
    for t in range(60):
        n = int(rp4.integers(1, 130))
        plain = [int(x) for x in rp4.integers(0, 65536, n)]
        ph0 = [int(x) for x in rp4.integers(0, 65536, n)]
        near = [int(min(n - 1, max(0, b + int(rp4.integers(-3, 4)))))
                for b in range(n)]
        mag = [int(rp4.integers(0, 32768)) for _ in range(n)]
        w = int(rp4.integers(0, 4))
        ct = int(rp4.integers(0, 4))
        if t < 6:
            w, ct = [0, 1, 2, 3, 2, 1][t], [0, 1, 2, 0, 1, 3][t]
            mag = [0] * n
            for k in range(0, n, 7):
                mag[k] = 32767
        got, _ = run_prop4(plain, ph0, near, mag, w, ct)
        want = model_prop4(plain,ph0,near,mag,w,ct)
        if got != want:
            bad += 1
            if bad < 3:
                i = next(j for j in range(n) if got[j] != want[j])
                print("PROP4 MISMATCH", n, "w", w, "ct", ct, i, got[i], want[i])
        assert bad == 0, bad
    print("PROP4: 60/60 exact (region-of-influence + spectral-contrast gate)")
    bad = 0
    rp5 = np.random.default_rng(0xD00D)
    for t in range(60):
        n = int(rp5.integers(1, 130))
        new = [int(x) for x in rp5.integers(0, 65536, n)]
        prev = [int(x) for x in rp5.integers(0, 65536, n)]
        near = [int(min(n - 1, max(0, b + int(rp5.integers(-3, 4)))))
                for b in range(n)]
        mag = [int(rp5.integers(0, 32768)) for _ in range(n)]
        w = int(rp5.integers(0, 4))
        ct = int(rp5.integers(0, 4))
        if t < 6:
            w, ct = [0, 1, 2, 3, 2, 1][t], [0, 1, 2, 0, 1, 3][t]
            mag = [0] * n
            for k in range(0, n, 7):
                mag[k] = 32767
        got, _ = run_prop5(new, prev, near, mag, w, ct)
        want = model_prop5(new, prev, near, mag, w, ct)
        if got != want:
            bad += 1
            if bad < 3:
                i = next(j for j in range(n) if got[j] != want[j])
                print("PROP5 MISMATCH", n, "w", w, "ct", ct, i, got[i], want[i])
    assert bad == 0, bad
    print("PROP5: 60/60 exact (stable-offset region-of-influence lock)")
    import math
    bad = 0
    for t in range(20):
        n = int(rng.integers(16, 512))
        x = [int(v) for v in
             (28000 * np.sin(2 * math.pi * 7 * np.arange(n) / n)
              + rng.integers(-2000, 2000, n))]
        x = [max(-32768, min(32767, int(v))) for v in x]
        w = [int(32767 * 0.5 * (1 - math.cos(2 * math.pi * i / n)))
             for i in range(n)]
        got, _ = run_hann(x, w)
        want = model_hann(x, w)
        if got != want:
            bad += 1
            if bad < 3:
                print("HANN MISMATCH", n, got[:6], want[:6])
    assert bad == 0, bad
    print("HANN: 20/20 exact")
    tab = build_sintab()
    bad = 0
    edge = [0, 1, 127, 128, 255, 256, 511, 512, 16383, 16384, 16511,
            32768, 49152, 65280, 65535]
    for t in range(30):
        n = int(rng.integers(8, 300))
        ph = [int(x) for x in rng.integers(0, 65536, n)]
        if t < 3:
            ph[:len(edge)] = edge[:min(len(edge), n)]
        gs, gc, _ = run_sincos(ph, tab)
        ws, wc = model_sincos(ph, tab)
        if gs != ws or gc != wc:
            bad += 1
            if bad < 3:
                print("SINCOS MISMATCH", ph[:8], gs[:6], ws[:6],
                      gc[:6], wc[:6])
    assert bad == 0, bad
    print("SINCOS: 30/30 exact")
    atab = build_atantab()
    bad, maxerr = 0, 0
    for t in range(40):
        n = int(rng.integers(8, 200))
        re = [int(x) for x in rng.integers(-32768, 32767, n)]
        im = [int(x) for x in rng.integers(-32768, 32767, n)]
        if t < 3:
            re[:8] = [0, 0, 32767, -32768, 1, -1, 0, 12345]
            im[:8] = [0, 1, 0, 0, 32767, -32768, -1, -12345]
        got, _ = run_atan2(re, im, atab)
        want = model_atan2(re, im, atab)
        if got != want:
            bad += 1
            if bad < 3:
                print("ATAN2 MISMATCH", re[:6], im[:6], got[:6], want[:6])
        for g, x0, y0 in zip(got, re, im):
            if x0 == 0 and y0 == 0:
                continue
            fa = math.atan2(y0, x0)
            if fa < 0:
                fa += 2 * math.pi
            fq = int(round(fa / (2 * math.pi) * 65536)) & 0xFFFF
            d = abs(g - fq)
            d = min(d, 65536 - d)
            maxerr = max(maxerr, d)
    assert bad == 0, bad
    print(f"ATAN2: 40/40 exact vs int model, max float err {maxerr} LSB")
    assert maxerr <= 32, maxerr
    bad = 0
    for t in range(40):
        n = int(rng.integers(8, 200))
        ps = [int(x) for x in rng.integers(-32768, 32767, n)]
        pc = [int(x) for x in rng.integers(-32768, 32767, n)]
        as_ = [int(x) for x in rng.integers(-32768, 32767, n)]
        ac = [int(x) for x in rng.integers(-32768, 32767, n)]
        trw = int(rng.integers(0, 32768))
        gr, gi, _ = run_mix(ps, pc, as_, ac, trw)
        wr, wi = model_mix(ps, pc, as_, ac, trw)
        if gr != wr or gi != wi:
            bad += 1
            if bad < 3:
                print("MIX MISMATCH", trw, gr[:6], wr[:6], gi[:6], wi[:6])
    assert bad == 0, bad
    print("MIX: 40/40 exact")
    maxerr = 0
    ill = 0
    for t in range(20):
        n = int(rng.integers(16, 128))
        prop = [int(x) for x in rng.integers(0, 65536, n)]
        ana = [int(x) for x in rng.integers(0, 65536, n)]
        trw = int(rng.integers(0, 32768))
        tfl = trw / 32768.0
        got, _ = run_blend(prop, ana, trw, tab, atab)
        for g, pp, aa in zip(got, prop, ana):
            pa = pp / 65536.0 * 2 * math.pi
            qa = aa / 65536.0 * 2 * math.pi
            m = ((1 - tfl) * complex(math.cos(pa), math.sin(pa))
                 + tfl * complex(math.cos(qa), math.sin(qa)))
            fa = math.atan2(m.imag, m.real)
            if fa < 0:
                fa += 2 * math.pi
            fq = int(round(fa / (2 * math.pi) * 65536)) & 0xFFFF
            d = abs(g - fq)
            d = min(d, 65536 - d)
            if abs(m) < 0.05:
                ill += 1
                continue
            maxerr = max(maxerr, d)
    print(f"BLEND: composed max float err {maxerr} LSB ({ill} ill-"
          "conditioned bins skipped)")
    assert maxerr <= 16, maxerr
    bad = 0
    edge_pairs = [(0, 0), (1, 0), (0, 1), (2, 0), (3, 0), (4, 0), (8, 0),
                  (9, 0), (15, 0), (16, 0), (17, 0), (255, 255), (256, 0),
                  (32767, 0), (0, 32767), (23170, 23170), (-32768, -32768),
                  (32767, 32767), (1, -1), (-1, 1)]
    for t in range(30):
        n = int(rng.integers(1, 120))
        re = [int(x) for x in rng.integers(-32768, 32768, n)]
        im = [int(x) for x in rng.integers(-32768, 32768, n)]
        if t < 3:
            for k, (a, b) in enumerate(edge_pairs):
                if k < n:
                    re[k], im[k] = a, b
        got, _ = run_mag(re, im)
        want = model_mag(re, im)
        if got != want:
            bad += 1
            if bad < 3:
                i = next(j for j in range(n) if got[j] != want[j])
                print("MAG MISMATCH at", i, re[i], im[i], got[i], want[i])
    # exhaustive small range: every (re,im) with |re|,|im| <= 40
    small = [(a, b) for a in range(-40, 41) for b in range(-40, 41)]
    for c in range(0, len(small), 512):
        chunk = small[c:c + 512]
        got, _ = run_mag([p[0] for p in chunk], [p[1] for p in chunk])
        want = model_mag([p[0] for p in chunk], [p[1] for p in chunk])
        if got != want:
            bad += 1
    assert bad == 0, bad
    print("MAG: 30/30 + exhaustive |re|,|im| <= 40 exact (integer sqrt)")
    bad = 0
    for t in range(40):
        n = int(rng.integers(1, 200))
        ma = [int(x) for x in rng.integers(0, 32768, n)]
        mb = [int(x) for x in rng.integers(0, 32768, n)]
        frac = int(rng.integers(0, 65536))
        if t < 3:
            frac = [0, 32768, 65535][t]
        got, _ = run_mag_lerp(ma, mb, frac)
        want = model_mag_lerp(ma, mb, frac)
        if got != want:
            bad += 1
            if bad < 3:
                print("MAGL MISMATCH", frac, got[:6], want[:6])
    assert bad == 0, bad
    print("MAGL: 40/40 exact (magnitude interpolation, Q16 frac)")
    bad = 0
    for t in range(40):
        n = int(rng.integers(1, 200))
        mag = [int(x) for x in rng.integers(0, 32768, n)]
        ph = [int(x) for x in rng.integers(0, 65536, n)]
        gs, gc, _ = run_sincos(ph, tab)
        gr, gi, _ = run_rect(mag, gs, gc)
        wr, wi = model_rect(mag, gs, gc)
        if gr != wr or gi != wi:
            bad += 1
            if bad < 3:
                print("RECT MISMATCH", gr[:6], wr[:6], gi[:6], wi[:6])
    assert bad == 0, bad
    print("RECT: 40/40 exact (polar -> complex resynthesis)")
    print("PV PHASE: PASS (peaks, nearest, propagate, hann, sincos,"
          " atan2, mix, blend, mag, magl, rect)")
    from fft_fixed import fft_q15, ifft_q15, twiddles_q15, s32
    bad = 0
    for n in (8, 32, 256):
        tw = twiddles_q15(n)
        for t in range(8 if n < 256 else 3):
            xr = [int(x) for x in rng.integers(-32768, 32767, n)]
            xi = [int(x) for x in rng.integers(-32768, 32767, n)]
            gr, gi, _ = run_fft(xr, xi, tw)
            wr, wi = fft_q15(list(xr), list(xi), tw)
            wr = [v - 65536 if v > 32767 else v for v in
                  [(v & 0xFFFF) for v in wr]]
            wi = [v - 65536 if v > 32767 else v for v in
                  [(v & 0xFFFF) for v in wi]]
            if gr != wr or gi != wi:
                bad += 1
                if bad < 3:
                    print("FFT MISMATCH", n, gr[:6], wr[:6], gi[:6], wi[:6])
    assert bad == 0, bad
    print("FFT: exact vs fft_q15 (N=8,32,256)")
    bad = 0
    for t in range(5):
        n = 64
        tw = twiddles_q15(n)
        xr = [int(x) for x in rng.integers(-20000, 20000, n)]
        xi = [int(x) for x in rng.integers(-20000, 20000, n)]
        gr, gi, _ = run_fft(xr, xi, tw)
        cr, _ = run_conj(gi)
        hr, hi, _ = run_fft(gr, cr, tw)
        br, _ = run_conj(hi)
        wr, wi = ifft_q15(gr, gi, tw)
        to16 = lambda v: ((v & 0xFFFF) - 65536 if (v & 0xFFFF) > 32767
                          else (v & 0xFFFF))
        if hr != [to16(v) for v in wr] or br != [to16(v) for v in wi]:
            bad += 1
            if bad < 3:
                print("IFFT MISMATCH", hr[:6], [to16(v) for v in wr][:6],
                      br[:6], [to16(v) for v in wi][:6])
    assert bad == 0, bad
    print("IFFT: conj-trick roundtrip exact vs ifft_q15")


if __name__ == "__main__":
    main()
