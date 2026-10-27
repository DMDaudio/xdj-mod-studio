/* ltentry.S -- light-mode text colour wrapper. The firmware's per-pixel
 * text blender FUN_08C2F5C2(en, dst, ..) takes its colour as a 4-byte
 * stack argument {R, G, B, A} at @(0,r15). Both pointers to it (pool words
 * 0x08C2F8FC, 0x08C31FC4) point here: we let lt_map() adjust that copy in
 * place, then jump to the real blender with every register intact. */
        .section .entry,"ax"
        .global _lt_head
        .align 2
_lt_head:
        .long   0x5448474C              /* "LGHT" */
        .long   _lt_entry
        .long   _lt_on
        .long   _lt_mset
        .long   _lt_rgb
        .long   _lt_flush
        .long   _lt_prev
        .long   _lt_pdk
        .text
        .global _lt_entry
        .align 2
_lt_entry:
        mov.l   .Lact,r0                /* light off: straight through */
        mov.l   @r0,r0
        tst     r0,r0
        bt      .Lskip
        sts.l   pr,@-r15
        mov.l   r4,@-r15
        mov.l   r5,@-r15
        mov.l   r6,@-r15
        mov.l   r7,@-r15
        mov     r15,r4
        add     #20,r4                  /* the colour argument */
        mov.l   .Lmap,r0
        jsr     @r0
        nop
        mov.l   @r15+,r7
        mov.l   @r15+,r6
        mov.l   @r15+,r5
        mov.l   @r15+,r4
        lds.l   @r15+,pr
.Lskip: mov.l   .Lorig,r0
        jmp     @r0
        nop
        .align 2
.Lact:  .long   _lt_active
.Lmap:  .long   _lt_map
.Lorig: .long   0x08C2F5C2

/* memset wrapper for the two waveform painters' pool words (0x08DC3B44
 * centre, 0x08DC2808 PERFORM strip): with lt_on, clearing THEIR staging
 * buffers uses lt_bg (byte, 0xFF = white) instead of 0 -- the waveform
 * window turns light. Every other memset passes through untouched. */
        .global _lt_mset
        .align 2
_lt_mset:
        sts.l   pr,@-r15
        mov.l   r4,@-r15
        mov.l   r5,@-r15
        mov.l   r6,@-r15
        mov.l   r7,@-r15
        mov.l   .Lmsv,r0
        jsr     @r0                     /* r0 = lt_msval(ptr, value) */
        nop
        mov     r0,r5
        mov.l   @r15+,r7
        mov.l   @r15+,r6
        add     #4,r15
        mov.l   @r15+,r4
        lds.l   @r15+,pr
        mov.l   .Lmemset,r0
        jmp     @r0
        nop
        .align 2
.Lmsv:    .long   _lt_msval
.Lmemset: .long   0x08E1D954

/* flush hook (pool 0x08C92E20 chain; r4 = dev, r5 = req): rewrite the
 * list's fill colours (lt_fill), then go on down the chain (lt_prev) */
        .global _lt_flush
        .align 2
_lt_flush:
        sts.l   pr,@-r15
        mov.l   r4,@-r15
        mov.l   r5,@-r15
        mov.l   r6,@-r15                /* the flush takes r6/r7 too (as */
        mov.l   r7,@-r15                /* tools/phasemeter/pmhook.S keeps) */
        mov     r5,r4
        mov.l   .Lfill,r0
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
.Lfill: .long   _lt_fill
.Lprev: .long   _lt_prev
        .data
        .global _lt_prev
        .align 2
_lt_prev: .long 0x08E7D278

        .text
/* lt_pdk -- ink flip for the PERFORM pads (fillrect / drawaa call it
 * through their PDKW word; tools/perform_pads.py). Pads convention:
 * colour in AND out of r2; r4-r7 preserved, only r0-r3 used. NEUTRAL
 * inks flip like text (lt_map's rule: v = 255 - mx*7/8; v = v*v>>8),
 * coloured inks pass unchanged. lt_active (refreshed every flush by
 * lt_fill) gates it, so the pads follow the theme switcher exactly. */
        .global _lt_pdk
        .align 2
_lt_pdk:
        mov.l   .Lact2,r0
        mov.l   @r0,r0
        tst     r0,r0
        bt      .Lpk_out
        mov.l   r2,@-r15               /* keep c: the coloured path returns it */
        mov     r2,r0
        and     #31,r0                 /* B = (c&31)*2 (and #imm is R0-only) */
        mov     r0,r3
        shll    r3
        mov     r2,r0                  /* G = (c>>5)&63 (spans the byte */
        shlr2   r0                     /* boundary: plain >>5, no shortcut) */
        shlr2   r0
        shlr    r0
        and     #63,r0
        mov     r2,r1                  /* R = (c>>11)*2, 6-bit */
        shlr8   r1
        shlr2   r1
        shlr    r1
        shll    r1
        mov     r1,r2                  /* mx(R,G,B) */
        cmp/gt  r2,r0
        bf      1f
        mov     r0,r2
1:      cmp/gt  r2,r3
        bf      2f
        mov     r3,r2
2:      cmp/gt  r0,r1                  /* mn(R,G,B) */
        bf      3f
        mov     r0,r1
3:      cmp/gt  r3,r1
        bf      4f
        mov     r3,r1
4:      sub     r1,r2                  /* spread; 12 (6-bit) ~ lt's 48 */
        mov     #12,r0
        cmp/hi  r0,r2
        bf      5f
        rts                            /* coloured: unchanged */
        mov.l   @r15+,r2               /* rts delay: restore c */
5:      add     r1,r2                  /* mx; 6 -> 8 bit */
        shll2   r2                     /* mx8 (lt.c: all in 8-bit) */
        mov     r2,r3
        shll2   r3
        shll    r3
        sub     r2,r3                  /* mx8 * 7 */
        shlr2   r3
        shlr    r3                     /* (mx8 * 7) >> 3 */
        mov     #-1,r1                 /* mov #imm8 is SIGNED: 255 = -1 */
        extu.b  r1,r1
        sub     r3,r1                  /* v8 = 255 - (mx8*7 >> 3) */
        mulu.w  r1,r1
        sts     macl,r1
        shlr8   r1                     /* v = v8*v8 >> 8 (grey 0..255) */
        mov     r1,r2
        shlr2   r2
        shlr    r2                     /* t = v>>3, 0..31 */
        mov     r1,r3
        shlr2   r3
        shll2   r3
        shll2   r3
        shll    r3                     /* (v>>2)<<5 = the 6-bit green term */
        mov     r2,r0
        shll8   r0
        shll2   r0
        shll    r0                     /* t<<11 */
        or      r3,r0
        or      r2,r0                  /* r0 = t | (v>>2)<<5 | t<<11 */
        mov     r0,r2
        rts
        mov.l   @r15+,r0               /* rts delay: drop the saved c */
.Lpk_out:
        rts
        nop
        .align 2
.Lact2: .long   _lt_active
