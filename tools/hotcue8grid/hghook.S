/* hghook.S -- entry table at HG_BASE (PROTOTYPE, emulator only):
 *  +0 flush hook  (pool 0x08C92E20): hg_hook(dev, req), then chain hg_bprev
 *  +4 touch hook  (pool 0x08D5278C): hg_touch(down, x, y); 2 = swallow,
 *                 else chain hg_tprev with the args untouched
 *  +8 hg_set_view(v)
 * Delay slots hold only nop or register moves (no PC-relative load, no
 * branch: CLAUDE.md section 119). */
        .section .entry,"ax"
        .global _hg_entry
        .global _hg_tentry
        .align 2
_hg_table:
        .long   _hg_entry
        .long   _hg_tentry
        .long   _hg_set_view
_hg_entry:
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
        mov.l   .Lbprev,r0
        mov.l   @r0,r0
        jmp     @r0
        nop
_hg_tentry:
        sts.l   pr,@-r15
        mov.l   r4,@-r15
        mov.l   r5,@-r15
        mov.l   r6,@-r15
        mov.l   r7,@-r15
        mov.l   .Ltouch,r0
        jsr     @r0
        nop
        mov.l   @r15+,r7
        mov.l   @r15+,r6
        mov.l   @r15+,r5
        mov.l   @r15+,r4
        lds.l   @r15+,pr
        cmp/eq  #2,r0
        bt      .Lswallow
        mov.l   .Ltprev,r0
        mov.l   @r0,r0
        jmp     @r0
        nop
.Lswallow:
        rts
        nop
        .align 2
.Lhook:  .long   _hg_hook
.Ltouch: .long   _hg_touch
.Lbprev: .long   _hg_bprev
.Ltprev: .long   _hg_tprev

        .section .data
        .global _hg_bprev
        .global _hg_tprev
        .align 2
_hg_bprev: .long 0x08E7D278     /* stock flush; installer overwrites */
_hg_tprev: .long 0x08DD0128     /* stock hit-tester; installer overwrites */
