/* pmhook.S -- display-flush hook entry: call pm_hook(dev, req) in C,
 * then chain to whatever the pool held before (perform_pads or stock
 * FUN_08E7D278) with the original arguments. pm_prev is set by the
 * installer to the pool's previous value. */
        .section .entry,"ax"
        .global _pm_entry
        .align 2
_pm_entry:
        sts.l   pr,@-r15
        mov.l   r4,@-r15
        mov.l   r5,@-r15
        mov.l   r6,@-r15
        mov.l   r7,@-r15
        mov.l   .Lhook,r0
        jsr     @r0
        nop
        mov.l   @r15+,r7
        mov.l   @r15+,r6
        mov.l   @r15+,r5
        mov.l   @r15+,r4
        lds.l   @r15+,pr
        mov.l   .Lprev,r0
        mov.l   @r0,r0
        jmp     @r0
        nop
        .align 2
.Lhook: .long   _pm_hook
.Lprev: .long   _pm_prev

        .section .data
        .global _pm_prev
        .align 2
_pm_prev: .long 0x08E7D278      /* stock flush; installer overwrites */
