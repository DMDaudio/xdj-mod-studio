/* vfstack.S -- the virtual-WAV layer's exported entry points (the slots the
 * installer points the deck's USB file ops at). Each one runs its C body
 * (vfc_*) on the layer's own 16 KB stack (vf_stack in vfile.c): dr_flac
 * needs ~4.5 KB just to open a file, more than the deck's track-loading
 * task has (2026-10-25 deck freeze on FLAC load, emulator-reproduced).
 * Arguments r4..r7 and the return value r0 pass straight through.
 * Claiming the stack is done with interrupts masked (no task switch between
 * test and set); if it is already in use (another task inside the layer)
 * the call runs on the caller's stack as before. */
        .text
        .align 2

        .macro ENTRY name, impl
        .global \name
\name:
        mov.l   1f,r0
        bra     vf_switch
        nop
        .align 2
1:      .long   \impl
        .endm

        ENTRY _vf_fread, _vfc_fread
        ENTRY _vf_fread_direct, _vfc_fread_direct
        ENTRY _vf_seek, _vfc_seek
        ENTRY _vf_tell, _vfc_tell
        ENTRY _vf_eof, _vfc_eof
        ENTRY _vf_size, _vfc_size
        ENTRY _vf_close, _vfc_close

/* r0 = C body; r4..r7 = its arguments */
vf_switch:
        stc     sr,r3                   /* mask interrupts while claiming */
        mov     r3,r2
        mov.l   .Limask,r1
        or      r1,r2
        ldc     r2,sr
        mov.l   .Lbusy,r1
        mov.l   @r1,r2
        tst     r2,r2
        bf      .Linuse
        mov     #1,r2
        mov.l   r2,@r1                  /* ours now */
        ldc     r3,sr                   /* interrupts back */
        mov     r15,r2                  /* caller's stack */
        mov.l   .Ltop,r15
        mov.l   r2,@-r15                /* old sp on the new stack */
        sts.l   pr,@-r15
        jsr     @r0
        nop
        lds.l   @r15+,pr
        mov.l   @r15,r2                 /* old sp */
        mov.l   .Lbusy,r1
        mov     #0,r3
        mov.l   r3,@r1                  /* free again (r0 = result kept) */
        rts
        mov     r2,r15
.Linuse:
        ldc     r3,sr                   /* nested / concurrent: as before */
        jmp     @r0
        nop

        .align 2
.Limask: .long  0x000000F0
.Lbusy: .long   _vf_stack_busy
.Ltop:  .long   _vf_stack + 16384
