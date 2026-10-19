/* ksrhook.S -- one-shot DSP reload trigger on the display-flush hook
 * (pool 0x08C92E20). Calls ksr_hook() in C, then chains to whatever the
 * pool held before (ksr_prev, set by the installer) with the original
 * arguments. ks_dsp = the DSP-side key-shift code (tools/keyshift_dsp/ks.c),
 * appended to the DSP program copy by ksr_hook. */
        .section .entry,"ax"
        .global _ksr_entry
        .align 2
_ksr_entry:
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
.Lhook: .long   _ksr_hook
.Lprev: .long   _ksr_prev

        .section .data
        .global _ksr_prev, _ksr_flag, _ksr_status, _ks_dsp, _ks_dsp_end
        .align 2
_ksr_prev:   .long 0x08E7D278    /* stock flush; installer overwrites */
_ksr_flag:   .long 0             /* installer writes 'KSR1' to request */
_ksr_status: .long 0             /* 1 = reloaded OK, see ksr.c         */
        .global _ksr_dbg
_ksr_dbg:    .long 0,0,0,0,0,0,0,0	/* diagnostic snapshots, see ksr.c  */
             .long 0,0,0,0,0,0,0,0
             .long 0,0,0,0,0,0,0,0             /* 24 words total          */
        .align 3
_ks_dsp:     .incbin "ks.bin"
_ks_dsp_end:
