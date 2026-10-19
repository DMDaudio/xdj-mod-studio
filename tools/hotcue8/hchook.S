/* hchook.S -- entry table at HC_BASE: +0 flush-hook entry (calls
 * hc_hook(dev, req) then chains to hc_prev), +4 hc_next (HOT CUE tab
 * re-tap, called from perform_pads). */
        .section .entry,"ax"
        .global _hc_entry
        .align 2
_hc_table:
        .long   _hc_entry
        .long   _hc_next
        .long   _hc_prev_page   /* +8 (2026-10-13: swipe right) */
_hc_entry:
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
.Lhook: .long   _hc_hook
.Lprev: .long   _hc_prev

        .section .data
        .global _hc_prev
        .align 2
_hc_prev: .long 0x08E7D278      /* stock flush; installer overwrites */
