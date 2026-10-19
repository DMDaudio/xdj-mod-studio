# CAVE PROOF v1 (deck injection primitive) -- DESIGNED 2026-10-03, not yet run

(Restored 2026-10-04 from session transcript after work/ was wiped by an
unknown tool at 10:42; moved to docs/ so it is not lost again. NOTE: the
RGB overview mod (tools/rgb_overview_apply.py) is now the planned FIRST
cave execution on hardware and supersedes this harness as "job one" --
same principles: zero-check, write+verify, hook last, reboot = stock.)

Goal: prove modified SH code executes on the physical XDJ-700:
original -> cave -> observable harmless proof -> original resumes.
RAM-only, fully restorable, reboot-proof (reboot wipes all).

## Hook: accept trampoline 0x08D53A24 (touch path, ghost-triggerable)
Original 6B (VERIFY on deck first, expect 4F 22 D1 29 0B 41):
  sts.l pr,@-r15 ; mov.l @(0x29,PC),r1 [pool 0x8D53ACC=0x8DA9096] ; jsr @r1
Patched 6B: D1 32 0B 41 09 00 =
  mov.l @(0x32,PC),r1 [pool 0x8D53AF0 = cave] ; jmp @r1 ; nop
- disp check: (0x8D53AF0-0x8D53A28)/4 = 0x32.
- Fires on every VALID touch -> trigger with proven ghost-tap (Vinyl).
  Touch keeps working (cave calls through to the original accept worker).

## Borrowed pool: 0x8D53AF0 (zero users, value looks like data)
- Original value MUST be read on deck first. Write cave address, restore
  original after. Never leave borrowed.

## Cave: 0x0A8BE608 (64B zero block, flanked, 0 writes/50M emu steps)
  cave: sts.l pr,@-r15 ; mov.l @(ACCEPT,pc),r1 ; jsr @r1 ; nop
        mov.l @(CNT,pc),r0 ; mov.l @r0,r0 ; add #1,r0
        mov.l @(CNT,pc),r1 ; mov.l r0,@r1
        mov.l @(BACK,pc),r1 ; jmp @r1 ; nop
  pools: ACCEPT=0x08DA9096 BACK=0x08D53A2A CNT=0x0A8BE63C
  (BUILD WITH THE FIXED shasm: before 2026-10-04 pool loads did not
  advance pc and branches/pools after them were off by 2 bytes each.
  Also `mov.l @(label,pc)` loads the LABEL ADDRESS; use numeric consts.)

## Protocol (solo, paced >=30s, never chained)
1. BR hook 6B (expect original) + pool 4B (record!) + cave 32B (expect 0).
2. BW pool <- cave addr. Verify.
3. BW hook 6B <- detour. Verify.
4. BW cave code + pools + counter=0. Verify all.
5. Ghost-tap Vinyl -- accept path runs cave.
6. BR counter (expect 1, then 2 after second tap).
7. RESTORE: hook original, pool original, cave zeros. Verify all.
8. Reboot anytime = stock (safety net, not the restore path).

## Risks (accepted, recoverable)
- Mid-patch execution (sequential 1B writes, non-atomic): no touching
  during steps 2-4. Worst case touch dies -> reboot restores.
- Cave clobbered by allocator: step 1 zero-check is the gate.
- Pool borrow: original restored step 7.
